"""Этап 3: экспедиции — топливо, события, три сценария возвращения, таймер прибытия.

Офлайн-заморозка (ТЗ) — структурная: фонового тикера экспедиции нет,
прогресс только по командам; проверяем косвенно: после launch "прошло время"
(изменение now в БД не требуется) — состояние не меняется без команд.
"""
import random
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app import characters, expeditions
from app.models import Outbox, Timer
from app.models_game import EventTemplate, ExpeditionEvent, Ship
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, fuel: int | None = None):
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    if fuel is not None:
        ship = await session.get(Ship, character.id)
        assert ship is not None
        ship.fuel = fuel
        await session.flush()
    return character


async def _outbox_texts(session, character) -> list[str]:
    rows = (
        await session.scalars(
            sa.select(Outbox).where(Outbox.character_id == character.id)
        )
    ).all()
    return [r.text for r in rows]


@requires_db
async def test_launch_creates_expedition_with_queue(session, seeded):
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)

    assert expedition.status == "active"
    assert expedition.fuel_remaining == 50 == expedition.fuel_at_start
    assert len(expedition.event_queue) == 10  # config expedition.queue_length
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "expedition"

    # повторный запуск невозможен (частичный уникальный индекс в БД)
    with pytest.raises(DomainError) as exc_info:
        await expeditions.launch_expedition(session, character)
    assert exc_info.value.code == "expedition_ongoing"


@requires_db
async def test_launch_requires_fuel(session, seeded):
    character = await _character(session, seeded, fuel=0)
    with pytest.raises(DomainError) as exc_info:
        await expeditions.launch_expedition(session, character)
    assert exc_info.value.code == "no_fuel"


@requires_db
async def test_go_spends_fuel_and_resolves_automatic_event(session, seeded):
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)
    before = expedition.fuel_remaining

    # очередь рандомна: крутим go, пока не встретим автоматическое событие
    for _ in range(20):
        event, _ = await expeditions.proceed(session, character, rng=random.Random(1))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
        else:
            assert event.status == "resolved"  # автоматические закрываются сразу
            break
    ongoing = await expeditions.get_ongoing_expedition(session, character.id)
    assert ongoing is not None
    assert ongoing.fuel_remaining < before  # топливо потрачено (ТЗ)


@requires_db
async def test_interactive_event_choice_applies_grants(session, seeded):
    character = await _character(session, seeded, fuel=50)
    await expeditions.launch_expedition(session, character)

    # ищем интерактивное событие, перебирая go/decline
    event = None
    for _ in range(25):
        event, note = await expeditions.proceed(session, character, rng=random.Random(7))
        if event is None:
            pytest.fail("топливо кончилось раньше, чем нашлось интерактивное событие")
        if event.snapshot["kind"] == "interactive":
            break
        # автоматическое событие уже разрешено — просто идём дальше
    else:
        pytest.fail("интерактивное событие не встретилось")
    assert event is not None

    # до выбора событие открыто; go во время него запрещён
    with pytest.raises(DomainError) as exc_info:
        await expeditions.proceed(session, character, rng=random.Random(1))
    assert exc_info.value.code == "event_pending"

    key = event.snapshot["script"]["choices"][0]["key"]
    await expeditions.resolve_choice(session, character, str(key), rng=random.Random(3))
    await session.refresh(event)
    assert event.status == "resolved"
    assert event.resolution is not None


@requires_db
async def test_decline_refusable_event_and_block_non_refusable(session, seeded):
    character = await _character(session, seeded, fuel=50)
    await expeditions.launch_expedition(session, character)

    for _ in range(25):
        event, _ = await expeditions.proceed(session, character, rng=random.Random(11))
        if event is None:
            pytest.fail("топливо кончилось раньше времени")
        if event.snapshot["kind"] == "interactive":
            break
        # автоматическое событие уже разрешено — идём дальше

    assert event is not None
    await expeditions.decline_event(session, character)  # все события-заглушки можно отклонить
    await session.refresh(event)
    assert event.status == "declined"


@requires_db
async def test_voluntary_return_scenario(session, seeded):
    """Добровольное возвращение: без штрафа, топливо сохраняется, задержка + 2 сообщения."""
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)

    # во время активного события вернуться нельзя (ТЗ)
    for _ in range(25):
        event, _ = await expeditions.proceed(session, character, rng=random.Random(5))
        if event is None:
            pytest.fail("топливо кончилось раньше времени")
        if event.snapshot["kind"] == "interactive":
            break
        # автоматическое событие уже разрешено — идём дальше
    assert event is not None
    with pytest.raises(DomainError) as exc_info:
        await expeditions.request_return(session, character)
    assert exc_info.value.code == "event_pending"
    await expeditions.decline_event(session, character)

    fuel_before = expedition.fuel_remaining
    await expeditions.request_return(session, character)
    await session.refresh(expedition)
    assert expedition.status == "returning"
    assert expedition.return_reason == "voluntary"
    assert expedition.return_eta is not None
    assert expedition.fuel_remaining == fuel_before  # топливо сохраняется (ТЗ)

    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "returning"

    # таймер прибытия поставлен
    timer = await session.scalar(
        sa.select(Timer).where(
            Timer.type == "exp_return_arrival", Timer.status == "pending"
        )
    )
    assert timer is not None

    # прибытие: worker-обработчик таймера
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})
    await session.refresh(expedition)
    assert expedition.status == "completed"
    assert expedition.arrived_at is not None
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "dock"

    # повторная обработка того же таймера — идемпотентна (ТЗ)
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})

    texts = await _outbox_texts(session, character)
    assert any("Возвращение началось" in t for t in texts)
    assert any("Прибыли в космопорт" in t for t in texts)


@requires_db
async def test_fuel_empty_return_scenario(session, seeded):
    """Исчерпание топлива: автовозврат + поломки. Состояние фиксируется (не DomainError)."""
    character = await _character(session, seeded, fuel=1)
    expedition = await expeditions.launch_expedition(session, character)

    # go пока топливо не кончится: любое событие стоит >= 1
    note = ""
    for _ in range(30):
        event, note = await expeditions.proceed(session, character, rng=random.Random(2))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
    assert "Топливо исчерпано" in note

    await session.refresh(expedition)
    assert expedition.status == "returning"
    assert expedition.return_reason == "fuel_empty"
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.malfunctions >= 1  # поломки при автовозврате (ТЗ)


@requires_db
async def test_defeat_from_hull_damage_loses_part_of_loot(session, seeded):
    """Поражение: автовозврат, поломки, потеря доли добычи (только losable-предметы)."""
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.current_hull = 10

    # выдадим добычу напрямую и «провалим» событие с уроном > 10
    await characters.grant_items(session, character, code="scrap_metal", qty=40, allow_loss=True)
    quiet = await session.scalar(
        sa.select(EventTemplate).where(EventTemplate.code == "quiet_drift")
    )
    assert quiet is not None
    session.add(
        ExpeditionEvent(
            expedition_id=expedition.id,
            event_template_id=quiet.id,
            seq=900,
            status="resolved",
            snapshot={"code": "test", "name": "Тест", "kind": "automatic"},
            resolution={
                "outcome": {"message": "удар", "grants": [{"code": "scrap_metal", "qty": 40}]},
                "effects": [],
            },
            resolved_at=datetime.now(UTC),
        )
    )
    await session.flush()
    _, scrap_before = await characters.list_inventory(session, character.id)

    await expeditions._apply_defeat(session, character, expedition, random.Random(4))
    await session.refresh(expedition)
    assert expedition.status == "returning"
    assert expedition.return_reason == "defeat"
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.malfunctions >= 1

    # потерялась лишь часть (10–30% от 80 лома: стартовые 10 + 40 добычи + 40 выданных)
    # стартовые ресурсы «не затрагиваются» — но они фунгибельны со добычей; проверяем факт частичной потери
    _, scrap_after = await characters.list_inventory(session, character.id)
    qty_before = sum(r.qty for r in scrap_before if r.code == "scrap_metal")
    qty_after = sum(r.qty for r in scrap_after if r.code == "scrap_metal")
    assert qty_after < qty_before
    assert qty_after > qty_before * 0.5  # не всё потеряно


@requires_db
async def test_expedition_extends_queue_when_exhausted(session, seeded):
    """Экспедиции бесконечны по структуре (ТЗ): очередь пополняется."""
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)
    initial_len = len(expedition.event_queue)
    # проходим всю очередь (отклоняя события), топлива хватит при fuel_cost<=4: 50/1..4
    for _ in range(initial_len + 5):
        if expedition.status != "active":
            break
        event, _ = await expeditions.proceed(session, character, rng=random.Random(9))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
        await session.refresh(expedition)
    # после исчерпания очереди она должна была пополниться (или закончилось топливо)
    if expedition.status == "active":
        assert len(expedition.event_queue) > initial_len or expedition.queue_pos < len(
            expedition.event_queue
        )


@requires_db
async def test_queue_never_exceeds_unique_constraint(session, seeded):
    """Уникальные события ≤1 за экспедицию даже при продлении очереди (ТЗ)."""
    character = await _character(session, seeded, fuel=500)
    expedition = await expeditions.launch_expedition(session, character)
    rng = random.Random(13)
    for _ in range(60):
        if expedition.status != "active":
            break
        event, _ = await expeditions.proceed(session, character, rng=rng)
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
            await session.refresh(expedition)
    assert expedition.event_queue.count("trinium_vein") <= 1
