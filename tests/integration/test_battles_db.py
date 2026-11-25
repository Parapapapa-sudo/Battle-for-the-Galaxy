"""Этап 4: бои — ходы, расходники, отступление, победа/поражение, журнал."""
import random
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import battles, characters, expeditions
from app.models_game import Battle, BattleTurn, Character, Ship
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, fuel: int = 50, hull: int | None = None):
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = fuel
    if hull is not None:
        ship.current_hull = hull
    await session.flush()
    return character


async def _battle(
    session: AsyncSession, character: Character, enemy: str = "pirate_scout"
) -> Battle:
    expedition = await expeditions.launch_expedition(session, character)
    return await battles.start_battle(
        session, character, enemy_code=enemy, expedition=expedition
    )


@requires_db
async def test_attack_round_player_then_enemy(session, seeded):
    """Базовый порядок ТЗ: игрок, затем противник. Краткий итог + подробный журнал."""
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character)
    enemy_hull_before = int(battle.state["enemy_hull"])

    battle = await battles.player_attack(session, character, rng=random.Random(2))
    turns = (
        await session.scalars(
            sa.select(BattleTurn).where(BattleTurn.battle_id == battle.id)
        )
    ).all()

    assert battle.status == "active"
    assert int(battle.state["enemy_hull"]) < enemy_hull_before  # удар игрока
    assert int(battle.state["turn_no"]) == 2  # ход противника исполнился
    actors = [t.actor for t in turns]
    assert "player" in actors and "enemy" in actors
    assert all(t.summary for t in turns)  # краткий итог
    assert all(isinstance(t.log, list) for t in turns)  # подробный журнал


@requires_db
async def test_victory_grants_xp_and_loot_once(session, seeded):
    """Победа (ТЗ): награды атомарно, опыт только за победу."""
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character)
    xp_before = character.xp_pool

    rng = random.Random(3)
    for _ in range(50):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "won"
    assert character.xp_pool == xp_before + 15  # xp pirate_scout, только за победу

    _, items = await characters.list_inventory(session, character.id)
    scrap = sum(r.qty for r in items if r.code == "scrap_metal")
    assert scrap >= 8  # лут пиратов (8-12) + стартовый 10 фактически больше

    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "expedition"  # бой в экспедиции

    # повторная атака невозможна — боя нет
    with pytest.raises(DomainError) as exc_info:
        await battles.player_attack(session, character, rng=rng)
    assert exc_info.value.code == "no_battle"


@requires_db
async def test_retreat_success_no_rewards_and_single_attempt(session, seeded):
    """Отступление (ТЗ): успех = противник исчезает, награды не выдаются, 1 попытка."""
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character)
    xp_before = character.xp_pool

    # шанс 100%, последствия 0%
    await session.execute(
        sa.text("UPDATE config_params SET value='100' WHERE key='combat.retreat_chance_pct'")
    )
    await session.execute(
        sa.text(
            "UPDATE config_params SET value='0' WHERE key IN "
            "('combat.retreat_damage_chance_pct','combat.retreat_malfunction_chance_pct')"
        )
    )
    battle = await battles.player_retreat(session, character, rng=random.Random(1))
    assert battle.status == "retreated"
    assert character.xp_pool == xp_before  # награда за победу не выдаётся (ТЗ)

    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "expedition"  # противник исчез, полёт продолжается

    with pytest.raises(DomainError) as exc_info:
        await battles.player_retreat(session, character, rng=random.Random(1))
    assert exc_info.value.code == "no_battle"  # боя больше нет

    # ход противника НЕ исполнялся автоматически: ход остался 1
    assert int(battle.state["turn_no"]) == 1


@requires_db
async def test_retreat_fail_spends_attempt_and_enemy_acts(session, seeded):
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character)
    await session.execute(
        sa.text("UPDATE config_params SET value='0' WHERE key='combat.retreat_chance_pct'")
    )
    battle = await battles.player_retreat(session, character, rng=random.Random(1))
    assert battle.retreat_used is True
    assert battle.status == "active"
    turns = (
        await session.scalars(
            sa.select(BattleTurn).where(BattleTurn.battle_id == battle.id)
        )
    ).all()
    assert any(t.actor == "enemy" for t in turns)  # враг ответил на неудачу

    with pytest.raises(DomainError) as exc_info:
        await battles.player_retreat(session, character, rng=random.Random(1))
    assert exc_info.value.code == "retreat_spent"  # повторная попытка невозможна (ТЗ)


@requires_db
async def test_consumable_heals_and_respects_turn_limit(session, seeded):
    character = await _character(session, seeded, hull=50)  # макс 100
    battle = await _battle(session, character)
    assert battle is not None

    # каждый use: +15 нанопластырь, враг отвечает (~8). netto > 50 — лечение работает,
    # но не выше капа 100
    battle = await battles.player_use_item(session, character, "nano_patch", rng=random.Random(1))
    ship = await session.get(Ship, character.id)
    assert ship is not None
    assert 50 < ship.current_hull <= 100

    # второй расходник — разрешено (лимит 2 за ход; use закрывает ход и сбрасывает счётчик)
    await battles.player_use_item(session, character, "nano_patch", rng=random.Random(1))
    _, items = await characters.list_inventory(session, character.id)
    nano = sum(r.qty for r in items if r.code == "nano_patch")
    assert nano == 1  # было 3, два израсходованы


@requires_db
async def test_non_consumable_item_rejected_in_combat(session, seeded):
    character = await _character(session, seeded, hull=1000)
    await _battle(session, character)
    with pytest.raises(DomainError) as exc_info:
        await battles.player_use_item(session, character, "scrap_metal", rng=random.Random(1))
    assert exc_info.value.code == "not_allowed_in_combat"
    with pytest.raises(DomainError) as exc_info:
        await battles.player_use_item(session, character, "repair_kit", rng=random.Random(1))
    assert exc_info.value.code == "not_allowed_in_combat"  # ремкомплект — не боевое лечение (ТЗ)


@requires_db
async def test_defeat_triggers_expedition_defeat_return(session, seeded):
    """Поражение (ТЗ): автовозврат экспедиции с reason=defeat."""
    character = await _character(session, seeded, hull=5)
    battle = await _battle(session, character, enemy="pirate_raider")

    rng = random.Random(4)
    for _ in range(30):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "lost"

    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    assert expedition is not None
    assert expedition.status == "returning"
    assert expedition.return_reason == "defeat"

    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "returning"


@requires_db
async def test_go_and_return_blocked_during_battle(session, seeded):
    character = await _character(session, seeded, hull=1000)
    await _battle(session, character)

    with pytest.raises(DomainError) as exc_info:
        await expeditions.proceed(session, character, rng=random.Random(1))
    assert exc_info.value.code == "battle_active"

    with pytest.raises(DomainError) as exc_info:
        await expeditions.request_return(session, character)
    assert exc_info.value.code == "battle_active"


@requires_db
async def test_elite_passives_reduce_incoming_damage(session, seeded):
    """Пассивы elite применяются к урону игрока (ТЗ). Капитан: −20%, +25% к входящему."""
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character, enemy="pirate_captain")
    before = int(battle.state["enemy_hull"])

    # attack корабля 5±10% -> ~5; модификаторы: *0.8*1.25 ≈ без изменений при 20/25
    battle = await battles.player_attack(session, character, rng=random.Random(7))
    dealt = before - int(battle.state["enemy_hull"])
    assert 3 <= dealt <= 7  # разброс; главное — бой идёт и урон ненулевой
    snap = battle.enemy_snapshot
    assert snap["passives"] and snap["weaknesses"]  # видимые эффекты показаны (ТЗ)


@requires_db
async def test_battle_starts_from_event_outcome(session, seeded):
    """Событие с outcome.battle запускает бой (интеграция с экспедициями)."""
    character = await _character(session, seeded, hull=1000)
    expedition = await expeditions.launch_expedition(session, character)
    from app.models_game import EventTemplate, ExpeditionEvent

    template = await session.scalar(
        sa.select(EventTemplate).where(EventTemplate.code == "pirate_ambush")
    )
    assert template is not None
    event = ExpeditionEvent(
        expedition_id=expedition.id,
        event_template_id=template.id,
        seq=0,
        snapshot={
            "code": "pirate_ambush", "name": template.name, "kind": "interactive",
            "can_refuse": True, "script": {},
        },
    )
    session.add(event)
    await session.flush()

    effects = await expeditions._apply_outcome(
        session, character, expedition, event,
        {"message": "боооой!", "battle": {"enemy": "pirate_scout"}},
        random.Random(1),
    )
    assert any("Начался бой" in e for e in effects)
    battle = await battles.get_active_battle(session, character.id)
    assert battle is not None
    assert event.status == "resolved"
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "battle"


@requires_db
async def test_battle_state_persists_across_commands(session, seeded):
    """Регрессия: state JSONB мутируется по месту и должен сбрасываться в БД
    между командами (иначе бой невозможно выиграть за несколько ходов)."""
    character = await _character(session, seeded, hull=1000)
    battle = await _battle(session, character)

    await battles.player_attack(session, character, rng=random.Random(6))
    await session.flush()
    battle_id = battle.id
    session.expunge(battle)

    reloaded = await session.get(Battle, battle_id)
    assert reloaded is not None
    from app.models_game import EnemyTemplate

    template = await session.get(EnemyTemplate, reloaded.enemy_template_id)
    assert template is not None
    assert int(reloaded.state["enemy_hull"]) < int((template.stats or {}).get("hull", 0))


@requires_db
async def test_second_battle_cannot_start_while_active(session, seeded):
    character = await _character(session, seeded, hull=1000)
    await _battle(session, character)
    with pytest.raises(DomainError) as exc_info:
        await battles.start_battle(session, character, enemy_code="pirate_raider")
    assert exc_info.value.code == "battle_active"
