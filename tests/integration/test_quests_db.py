"""Этап 8: цепочка обучения (обязательное первое задание) и фракционные события."""
import random
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app import battles, characters, crafting, economy, expeditions, quests
from app.bus import CommandBus
from app.models_game import (
    Character,
    CharacterQuest,
    ExpeditionEvent,
    FactionEventParticipant,
    QuestTemplate,
    Ship,
)
from app.timer_handlers import build_timer_handlers
from tests.conftest import requires_db


async def _character(session, seeded) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 500
    await session.flush()
    return character


async def _active_quest(session, character) -> tuple[CharacterQuest, QuestTemplate] | None:
    rows = await quests.active_quests(session, character)
    return rows[0] if rows else None


async def _run_due(session, bus=None) -> int:
    bus = bus or CommandBus()
    handlers = await build_timer_handlers()
    return int(await bus.run_due_timers(timer_handlers=handlers, session=session))


async def _go(
    session, character, expedition, rng: random.Random | None = None
) -> ExpeditionEvent | None:
    """«Исследовать» + мгновенное прибытие события (в обход задержки пути)."""
    rng = rng or random.Random(1)
    await expeditions.proceed(session, character, rng=rng)
    await session.refresh(expedition)
    if expedition.status != "active" or expedition.next_event_at is None:
        return None
    next_seq = expedition.queue_pos  # seq события, которое создаст arrive_event
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.arrive_event(session, {"expedition_id": str(expedition.id)}, rng=rng)
    # None, если событие не создано (автовозврат по топливу)
    found: ExpeditionEvent | None = await session.scalar(
        sa.select(ExpeditionEvent).where(
            ExpeditionEvent.expedition_id == expedition.id,
            ExpeditionEvent.seq == next_seq,
        )
    )
    return found


@requires_db
async def test_first_quest_active_at_creation(session, seeded):
    """ТЗ: первое сюжетное задание обязательно — активируется при создании."""
    character = await _character(session, seeded)
    active = await _active_quest(session, character)
    assert active is not None
    state, quest = active
    assert quest.code == "tutorial_1"


@requires_db
async def test_tutorial_chain_progression(session, seeded, bus):
    """Цепочка: вылет → события → победа → крафт; награды атомарны."""
    character = await _character(session, seeded)

    # шаг 1: вылет
    expedition = await expeditions.launch_expedition(session, character)
    state, quest = (await quests.active_quests(session, character))[0]
    assert quest.code == "tutorial_2"  # tutorial_1 завершено, активно следующее
    assert character.xp_pool >= 10

    # шаг 2: два разрешённых события (отклонения не считаются — считаем resolved)
    for attempt in range(40):
        event = await _go(session, character, expedition, rng=random.Random(3))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            # выбираем вариант БОЯ, если он есть (tutorial_3 засчитается досрочно),
            # иначе — первый безопасный вариант
            choices = event.snapshot["script"]["choices"]
            battle_choice = next(
                (c for c in choices if "battle" in (c.get("outcome") or {})), None
            )
            safe_choice = next(
                (c for c in choices if "battle" not in (c.get("outcome") or {})), choices[0]
            )
            chosen = battle_choice if battle_choice is not None else safe_choice
            await expeditions.resolve_choice(
                session, character, str(chosen["key"]), rng=random.Random(1)
            )
            if battle_choice is not None:
                break  # бой начался — tutorial_2 добираем после победы
        if attempt + 1 >= 2:
            break
    active = await quests.active_quests(session, character)
    # tutorial_3 — после двух событий; но засада пиратов могла начать бой на шаге 2
    # раньше второго события — тогда добор произойдёт после победы
    assert active and active[0][1].code in ("tutorial_2", "tutorial_3")

    # шаг 3: победа в бою (засада могла начать бой уже на шаге 2)
    # события шага 2 могли рандомно повредить корпус — чиним, чтобы бой был детерминирован
    ship = await session.get(Ship, character.id)
    assert ship is not None
    from core.hull import max_hull

    ship.current_hull = max_hull(ship.base_max_hull, ship.malfunctions)
    await session.flush()
    battle = await battles.get_active_battle(session, character.id)
    if battle is None:
        battle = await battles.start_battle(
            session, character, enemy_code="pirate_scout", expedition=expedition
        )
    rng = random.Random(5)
    for _ in range(50):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "won"

    # если бой начался на шаге 2 — добираем недостающие события для tutorial_2
    for _ in range(10):
        active = await quests.active_quests(session, character)
        if not active or active[0][1].code != "tutorial_2":
            break
        event = await _go(session, character, expedition, rng=random.Random(8))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            safe = next(
                (c for c in event.snapshot["script"]["choices"] if "battle" not in (c.get("outcome") or {})),
                event.snapshot["script"]["choices"][-1],
            )
            await expeditions.resolve_choice(
                session, character, str(safe["key"]), rng=random.Random(1)
            )

    # прогресс засчитывается только АКТИВНОМУ заданию: если победа в бою случилась
    # до активации tutorial_3, дерёмся ещё раз — уже под активным заданием
    for _ in range(3):
        active = await quests.active_quests(session, character)
        if not active or active[0][1].code != "tutorial_3":
            break
        ship.current_hull = max_hull(ship.base_max_hull, ship.malfunctions)
        await session.flush()
        battle = await battles.start_battle(
            session, character, enemy_code="pirate_scout", expedition=expedition
        )
        rng = random.Random(5)
        for _ in range(50):
            battle = await battles.player_attack(session, character, rng=rng)
            if battle.status != "active":
                break
        assert battle.status == "won"

    active = await quests.active_quests(session, character)
    assert active and active[0][1].code == "story_1"

    # перед крафтом — возвращаемся в порт (производство только там)
    await expeditions.request_return(session, character)
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})

    # шаг 4: крафт (завершает цепочку)
    job = await crafting.start_craft(session, character, "craft_nano_patch")
    job.finishes_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        sa.update(__import__("app.models", fromlist=["Timer"]).Timer)
        .where(
            __import__("app.models", fromlist=["Timer"]).Timer.type == "craft_done",
            __import__("app.models", fromlist=["Timer"]).Timer.payload["job_id"].astext == str(job.id),
        )
        .values(fires_at=job.finishes_at)
    )
    await session.flush()
    await _run_due(session, bus)
    active = await quests.active_quests(session, character)
    assert active == []  # цепочка завершена
    completed = (
        await session.scalars(
            sa.select(CharacterQuest).where(
                CharacterQuest.character_id == character.id, CharacterQuest.status == "completed"
            )
        )
    ).all()
    assert len(completed) == 4


@requires_db
async def test_faction_event_common_goal_and_contributions(session, seeded):
    """ТЗ: общие цели + индивидуальный вклад; ранний финиш при достижении цели."""
    c1 = await _character(session, seeded)
    c2 = await _character(session, seeded)
    now = datetime.now(UTC)
    event = await quests.schedule_faction_event(
        session,
        name="Сбор триния",
        config={
            "goal": {"type": "event_resolved", "count": 3},
            "rewards": {"quant": 500, "xp": 20},
        },
        starts_at=now - timedelta(seconds=1),
        ends_at=now + timedelta(hours=1),
    )
    await quests.faction_event_start_handler(session, {"event_id": str(event.id)})
    await session.refresh(event)
    assert event.status == "active"

    # каждый крутит события; трекинг идёт через track()
    for character in (c1, c2):
        await expeditions.launch_expedition(session, character)
    await quests.track(session, c1, "event_resolved", {"code": "x"})
    await quests.track(session, c1, "event_resolved", {"code": "y"})
    await session.refresh(event)
    assert event.status == "active"  # 2/3

    await quests.track(session, c2, "event_resolved", {"code": "z"})
    await session.refresh(event)
    assert event.status == "finished"  # цель достигнута — ранний финиш (ТЗ)

    for character in (c1, c2):
        participant = await session.get(FactionEventParticipant, (event.id, character.id))
        assert participant is not None
        assert participant.rewards_granted
        balance, _ = await _balances(session, character)
        assert balance == 700  # 200 (обучение) + 500 награда события


async def _balances(session, character) -> tuple[int, int]:
    wallet = await economy.get_wallet(session, character.id, economy.QUANT)
    return wallet.balance, wallet.reserved


@requires_db
async def test_faction_event_expires_without_goal(session, seeded):
    character = await _character(session, seeded)
    now = datetime.now(UTC)
    event = await quests.schedule_faction_event(
        session,
        name="Недостижимое",
        config={"goal": {"type": "event_resolved", "count": 100}, "rewards": {"quant": 500}},
        starts_at=now - timedelta(seconds=1),
        ends_at=now + timedelta(hours=1),
    )
    await quests.faction_event_start_handler(session, {"event_id": str(event.id)})
    await quests.track(session, character, "event_resolved", {"code": "a"})

    await quests.faction_event_end_handler(session, {"event_id": str(event.id)})
    await session.refresh(event)
    assert event.status == "finished"
    participant = await session.get(FactionEventParticipant, (event.id, character.id))
    assert participant is not None and not participant.rewards_granted  # цель не достигнута
    balance, _ = await _balances(session, character)
    assert balance == 0
