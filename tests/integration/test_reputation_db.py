"""Репутация у фракций: стадии, источники (убийства/события), вступление, виды фракций."""
import random
import uuid

import pytest
import sqlalchemy as sa

from app import battles, characters, expeditions, reputation
from app.models_game import Character, Faction, Reputation, Ship
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, faction: str = "azimuth") -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Реп{uuid.uuid4().hex[:6]}", faction_code=faction
    )


async def _rep(session, character, faction_code: str) -> int | None:
    faction = await session.scalar(sa.select(Faction).where(Faction.code == faction_code))
    assert faction is not None
    row = await session.get(Reputation, (character.id, faction.id))
    return row.value if row else None


@requires_db
async def test_faction_quest_offer_flow(session, seeded):
    """Посланник нейтральной фракции: 100% шанс (config) → предложение; принятие
    активирует квест; выполнение даёт награду и репутацию с фракцией."""
    from app.models_game import CharacterQuest, QuestTemplate

    character = await _character(session, seeded)
    # форсируем появление посланника
    from app.models import ConfigParam

    param = await session.get(ConfigParam, "expedition.faction_quest_chance_pct")
    if param is None:
        session.add(ConfigParam(key="expedition.faction_quest_chance_pct", value=100))
    else:
        param.value = 100
    await session.flush()

    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 50
    await session.flush()
    expedition = await expeditions.launch_expedition(session, character)
    await expeditions.proceed(session, character, rng=random.Random(1))
    from datetime import UTC, datetime, timedelta

    await session.refresh(expedition)
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.arrive_event(
        session, {"expedition_id": str(expedition.id)}, rng=random.Random(1)
    )
    offered = await expeditions._current_offered_event(session, expedition)
    assert offered is not None
    assert offered.snapshot["code"] == "faction_quest_offer"
    assert "Посланник" in offered.snapshot["name"]

    # принимаем
    await expeditions.resolve_choice(session, character, "accept", rng=random.Random(1))
    active = (
        await session.execute(
            sa.select(QuestTemplate)
            .join(CharacterQuest, CharacterQuest.quest_id == QuestTemplate.id)
            .where(
                CharacterQuest.character_id == character.id,
                CharacterQuest.status == "active",
                QuestTemplate.kind == "faction",
            )
        )
    ).scalar_one_or_none()
    assert active is not None
    assert character.tracked_quest == active.code
    faction = await session.get(Faction, active.faction_id)
    assert faction is not None and faction.kind == "neutral"

    # выполняем цель квеста напрямую через track и проверяем награды
    from app import quests as quests_mod

    goal = active.objectives[0]
    for _ in range(int(goal.get("count", 1))):
        await quests_mod.track(session, character, str(goal["type"]))
    state = await session.scalar(
        sa.select(CharacterQuest).where(
            CharacterQuest.character_id == character.id,
            CharacterQuest.quest_id == active.id,
        )
    )
    assert state is not None and state.status == "completed"
    # репутация из наград начислена своей фракции квеста
    rep = await _rep(session, character, faction.code)
    assert rep == 500


@requires_db
async def test_chain_quest_grants_own_faction_reputation(session, seeded):
    """Задания цепочки (Штаб) улучшают репутацию со своей фракцией (+250)."""
    from app import quests as quests_mod

    character = await _character(session, seeded, faction="helion")
    await quests_mod.accept_quest(session, character, "tutorial_1")
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 50
    await session.flush()
    await expeditions.launch_expedition(session, character)  # цель tutorial_1
    assert await _rep(session, character, "helion") == 250  # quests.own_faction_rep


@requires_db
async def test_admin_enemy_editor(session, seeded):
    """Редактор врагов: создание с фракцией/репутацией, правка, защита от удаления."""
    from app import admin
    from app.models_game import AdminUser

    owner = await session.scalar(sa.select(AdminUser).where(AdminUser.login == "owner"))
    assert owner is not None

    created = await admin.create_content(session, owner, "enemy", {
        "code": "test_marauder", "name": "Тестовый мародёр", "category": "dangerous",
        "faction": "red_syndicate",
        "stats": {"hull": 60, "damage": 10, "xp": 25},
        "reputation": {"red_syndicate": -40},
    })
    assert created["faction"] == "red_syndicate"
    assert created["reputation"]["red_syndicate"] == -40

    updated = await admin.update_content(session, owner, "enemy", "test_marauder", {
        "name": "Мародёр v2", "category": "elite", "faction": "pirates",
        "stats": {"hull": 80, "damage": 12, "xp": 30},
        "reputation": {"pirates": -60},
    })
    assert updated["category"] == "elite" and updated["faction"] == "pirates"

    # валидация: кривая категория и stats без hull
    for bad in (
        {"name": "X", "category": "godlike", "stats": {"hull": 1, "damage": 1}},
        {"name": "X", "category": "normal", "stats": {"damage": 1}},
    ):
        with pytest.raises(DomainError):
            await admin.create_content(session, owner, "enemy", {"code": "bad_e", **bad})

    # свободный враг удаляется
    result = await admin.delete_content(session, owner, "enemy", "test_marauder")
    assert result["mode"] == "hard"

    # враг, участвовавший в боях, — защищён от удаления
    fighter = await _character(session, seeded)
    await battles.start_battle(session, fighter, enemy_code="pirate_scout")
    with pytest.raises(DomainError) as exc_info:
        await admin.delete_content(session, owner, "enemy", "pirate_scout")
    assert exc_info.value.code == "bad_content" and "боях" in exc_info.value.message


@requires_db
async def test_stages_boundaries(session, seeded):
    """Границы стадий: ненависть < -10000 ≤ враждебность < 0 ≤ нейтралитет < 10000 ≤
    дружелюбие < 50000 ≤ превознесение."""
    cases = [
        (-10001, "Ненависть"),
        (-10000, "Враждебность"),
        (-1, "Враждебность"),
        (0, "Нейтралитет"),
        (9999, "Нейтралитет"),
        (10000, "Дружелюбие"),
        (49999, "Дружелюбие"),
        (50000, "Превознесение"),
    ]
    for value, expected in cases:
        assert await reputation.stage_label(session, value) == expected, value


@requires_db
async def test_join_sets_own_and_penalizes_other_main_factions(session, seeded):
    """Вступление: своя фракция — start_value, остальные MAIN — штраф, нейтральных нет."""
    character = await _character(session, seeded, faction="helion")
    assert await _rep(session, character, "helion") == 0
    for other in ("azimuth", "cinder", "lumen"):
        assert await _rep(session, character, other) == -1000  # faction.join_penalty
    assert await _rep(session, character, "pirates") is None  # нейтральные — с нуля


@requires_db
async def test_neutral_faction_not_joinable(session, seeded):
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    with pytest.raises(DomainError) as exc_info:
        await characters.create_character(
            session, account_id=account.id, name=f"Пират{uuid.uuid4().hex[:5]}",
            faction_code="pirates",
        )
    assert exc_info.value.code == "faction_not_joinable"


@requires_db
async def test_victory_grants_quants_and_item_drops(session, seeded):
    """Победа: всегда кванты (диапазон конфига) + дроп предметов по их % (админка)."""
    from app import economy
    from app.models_game import ItemTemplate

    character = await _character(session, seeded)
    # настраиваем предмет: 100% дроп в бою, 2 шт
    tpl = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == "trinium_ore"))
    assert tpl is not None
    tpl.battle_drop_weight = 100
    tpl.drop_qty_min = tpl.drop_qty_max = 2
    await session.flush()

    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.base_max_hull = 2000
    ship.current_hull = 2000
    await session.flush()
    before = (await economy.get_wallet(session, character.id, economy.QUANT)).balance

    battle = await battles.start_battle(session, character, enemy_code="pirate_scout")
    rng = random.Random(5)
    for _ in range(60):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "won"

    after = (await economy.get_wallet(session, character.id, economy.QUANT)).balance
    assert 15 <= after - before <= 25  # combat.victory_quant_min/max
    _, items = await characters.list_inventory(session, character.id)
    trinium = sum(r.qty for r in items if r.code == "trinium_ore")
    assert trinium == 2  # гарантированный боевой дроп
    # строка наград содержит кванты
    outcome = str(battle.state.get("outcome_message", ""))
    assert "квантов" in outcome
    tpl.battle_drop_weight = 0  # откат
    await session.flush()


@requires_db
async def test_enemy_kill_grants_reputation(session, seeded):
    """Победа над пиратом снижает репутацию с Пиратами (enemy.reputation)."""
    character = await _character(session, seeded)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.base_max_hull = 2000
    ship.current_hull = 2000
    await session.flush()
    battle = await battles.start_battle(session, character, enemy_code="pirate_scout")
    rng = random.Random(5)
    for _ in range(60):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "won"
    assert await _rep(session, character, "pirates") == -25  # scout: -25


@requires_db
async def test_event_outcome_reputation(session, seeded):
    """Исход события может давать/отнимать репутацию (outcome.reputation)."""
    character = await _character(session, seeded)
    result = await reputation.grant(session, character, "veil_keepers", 150)
    assert result is not None
    name, value = result
    assert name == "Хранители завесы" and value == 150
    lines = await reputation.apply_deltas(session, character, {"red_syndicate": -300})
    assert lines and "Красный синдикат" in lines[0]
    assert await _rep(session, character, "red_syndicate") == -300
    # стадия в строке профиля
    block = await reputation.describe_reputations(session, character)
    assert "Хранители завесы" in block and "Нейтралитет" in block
    assert "Враждебность" in block  # red_syndicate -300
