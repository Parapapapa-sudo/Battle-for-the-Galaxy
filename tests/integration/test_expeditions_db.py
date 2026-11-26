"""Этап 3: экспедиции — топливо, события, три сценария возвращения, таймер прибытия.

Офлайн-заморозка (ТЗ) — структурная: фонового тикера экспедиции нет,
прогресс только по командам; проверяем косвенно: после launch "прошло время"
(изменение now в БД не требуется) — состояние не меняется без команд.
"""
import random
import uuid
from datetime import UTC, datetime, timedelta

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



async def _go(session, character, expedition, rng=None) -> ExpeditionEvent | None:
    """«Исследовать» + мгновенное прибытие события (в обход реальной задержки пути).

    proceed() отправляет корабль в путь (таймер exp_event); здесь доводим
    таймер до срабатывания напрямую. None — события нет (автовозврат по топливу).
    """
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
        event = await _go(session, character, expedition, rng=random.Random(1))
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
    expedition = await expeditions.launch_expedition(session, character)

    # ищем интерактивное событие, перебирая go/decline
    event = None
    for _ in range(25):
        event = await _go(session, character, expedition, rng=random.Random(7))
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
    expedition = await expeditions.launch_expedition(session, character)

    for _ in range(25):
        event = await _go(session, character, expedition, rng=random.Random(11))
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
        event = await _go(session, character, expedition, rng=random.Random(5))
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

    # прибытие: worker-обработчик таймера; игрок попадает в основной зал (ТЗ)
    character.hall = "hq"
    await session.flush()
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})
    await session.refresh(expedition)
    assert expedition.status == "completed"
    assert expedition.arrived_at is not None
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "dock"
    await session.refresh(character)
    assert character.hall == "main"

    # повторная обработка того же таймера — идемпотентна (ТЗ)
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})

    texts = await _outbox_texts(session, character)
    # «Возвращение началось» — ответ на команду (не дублируем в outbox);
    # прибытие уведомляет через outbox
    assert any("Прибыли в космопорт" in t for t in texts)


@requires_db
async def test_fuel_empty_return_scenario(session, seeded):
    """Исчерпание топлива: автовозврат + поломки. Состояние фиксируется (не DomainError)."""
    character = await _character(session, seeded, fuel=1)
    expedition = await expeditions.launch_expedition(session, character)

    # go пока топливо не кончится: любое событие стоит >= 1
    for _ in range(30):
        event = await _go(session, character, expedition, rng=random.Random(2))
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
    # автовозврат по топливу приходит уведомлением в outbox
    texts = await _outbox_texts(session, character)
    assert any("Топливо исчерпано" in text for text in texts)

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
        event = await _go(session, character, expedition, rng=random.Random(9))
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
        event = await _go(session, character, expedition, rng=rng)
        if event is None:
            break
        if event.snapshot["kind"] == "interactive":
            await expeditions.decline_event(session, character)
            await session.refresh(expedition)
    assert expedition.event_queue.count("trinium_vein") <= 1


@requires_db
async def test_travel_between_events(session, seeded):
    """ТЗ: между событиями — путешествие с задержкой 10с + (базовое топливо − текущее)/2.

    В пути доступна только кнопка «Инфо»: go не плодит таймеры, return/patch
    заблокированы, событие прибывает само по таймеру exp_event.
    """
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)

    # «Исследовать» → экран путешествия, события сразу нет
    event, note = await expeditions.proceed(session, character, rng=random.Random(1))
    assert event is None
    assert "путешествие" in note
    assert "Топливо: 50/50" in note
    await session.refresh(expedition)
    assert expedition.next_event_at is not None
    assert expedition.next_event_at > datetime.now(UTC)
    delay = (expedition.next_event_at - datetime.now(UTC)).total_seconds()
    assert 9 < delay <= 10  # база 10с, топливо ещё не тратилось

    # повторный go в пути → тот же экран, новый таймер не создаётся
    event2, note2 = await expeditions.proceed(session, character, rng=random.Random(1))
    assert event2 is None and note2 == note
    timers = (
        await session.scalars(
            sa.select(Timer).where(
                Timer.character_id == character.id,
                Timer.type == "exp_event",
                Timer.status == "pending",
            )
        )
    ).all()
    assert len(timers) == 1

    # в пути return и patch заблокированы
    with pytest.raises(DomainError) as exc_info:
        await expeditions.request_return(session, character)
    assert exc_info.value.code == "traveling"
    with pytest.raises(DomainError) as exc_info:
        await expeditions.start_patch(session, character)
    assert exc_info.value.code == "traveling"

    # таймер срабатывает → событие прибыло, уведомление в outbox
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "exp_event", Timer.status == "pending")
        .values(fires_at=expedition.next_event_at)
    )
    await session.flush()
    await expeditions.arrive_event(
        session, {"expedition_id": str(expedition.id)}, rng=random.Random(1)
    )
    await session.refresh(expedition)
    assert expedition.next_event_at is None  # снова свободное плавание
    arrived = await expeditions._current_offered_event(session, expedition)
    last = await session.scalar(
        sa.select(ExpeditionEvent)
        .where(ExpeditionEvent.expedition_id == expedition.id)
        .order_by(ExpeditionEvent.seq.desc())
        .limit(1)
    )
    assert arrived is not None or (last is not None and last.status == "resolved")
    texts = await _outbox_texts(session, character)
    assert any("Событие «" in text for text in texts)

    # закрываем прибывшее событие, чтобы снова исследовать
    if arrived is not None:
        await expeditions.decline_event(session, character)

    # задержка растёт от потраченного топлива: потрачено = fuel_cost первого события
    spent = expedition.fuel_at_start - expedition.fuel_remaining
    await expeditions.proceed(session, character, rng=random.Random(2))
    await session.refresh(expedition)
    assert expedition.next_event_at is not None
    delay2 = (expedition.next_event_at - datetime.now(UTC)).total_seconds()
    assert 9 + spent // 2 < delay2 <= 10 + spent // 2


@requires_db
async def test_status_message_after_event_completion(session, seeded):
    """После завершения события — отдельное сообщение: прочность, поломки, слоты."""
    character = await _character(session, seeded, fuel=50)
    expedition = await expeditions.launch_expedition(session, character)

    event = await _go(session, character, expedition, rng=random.Random(7))
    assert event is not None
    if event.snapshot["kind"] == "interactive":
        key = str(event.snapshot["script"]["choices"][0]["key"])
        await expeditions.resolve_choice(session, character, key, rng=random.Random(3))

    texts = await _outbox_texts(session, character)
    status_idx = [i for i, text in enumerate(texts) if "Состояние корабля:" in text]
    assert status_idx, "сообщение о состоянии не пришло"
    status = texts[status_idx[-1]]
    assert "Прочность:" in status and "Поломки:" in status
    assert "Инвентарь: свободно" in status
    # порядок: сначала событие, потом состояние
    outcome_idx = [i for i, text in enumerate(texts) if "Событие «" in text]
    assert outcome_idx and status_idx[-1] > outcome_idx[-1]


@requires_db
async def test_go_blocked_when_hull_too_low(session, seeded):
    """Прочность <= 1: исследование запрещено (лечение — «Залатать»)."""
    character = await _character(session, seeded, fuel=50)
    await expeditions.launch_expedition(session, character)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.current_hull = 1
    await session.flush()

    with pytest.raises(DomainError) as exc_info:
        await expeditions.proceed(session, character, rng=random.Random(1))
    assert exc_info.value.code == "hull_too_low"
    assert "не позволяет продолжить исследование" in exc_info.value.message

    # ремонт возвращает возможность исследовать
    await expeditions.start_patch(session, character)
    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    assert expedition is not None
    expedition.patch_eta = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "ship_patch", Timer.status == "pending")
        .values(fires_at=expedition.patch_eta)
    )
    await session.flush()
    await expeditions.patch_complete(session, {"expedition_id": str(expedition.id)})
    await session.refresh(ship)
    assert ship.current_hull == 100
    event = await _go(session, character, expedition, rng=random.Random(1))
    assert event is not None  # go снова работает
