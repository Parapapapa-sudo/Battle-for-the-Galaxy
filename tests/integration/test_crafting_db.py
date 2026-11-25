"""Этап 6: крафт (материалы, слоты, таймеры, отмена, «Ожидает получения») и склад."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, crafting, expeditions
from app.bus import CommandBus
from app.models import Outbox, Timer
from app.models_game import Character, Ship
from app.timer_handlers import build_timer_handlers
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, scrap: int = 0, fuel: int = 50) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = fuel
    if scrap:
        await characters.grant_items(session, character, code="scrap_metal", qty=scrap, allow_loss=True)
    await session.flush()
    return character


async def _backdate_job(session: AsyncSession, job) -> None:
    """Ускорение заказа: job и его таймер в прошлое."""
    job.finishes_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "craft_done", Timer.payload["job_id"].astext == str(job.id))
        .values(fires_at=job.finishes_at)
    )
    await session.flush()


async def _run_due(session: AsyncSession, bus: CommandBus | None = None) -> int:
    bus = bus or CommandBus()
    handlers = await build_timer_handlers()
    return await bus.run_due_timers(timer_handlers=handlers, session=session)


@requires_db
async def test_start_craft_consumes_materials_and_schedules_timer(session, seeded):
    character = await _character(session, seeded, scrap=10)
    job = await crafting.start_craft(session, character, "craft_nano_patch")

    assert job.status == "running"
    assert job.finishes_at > datetime.now(UTC)
    _, items = await characters.list_inventory(session, character.id)
    scrap = sum(r.qty for r in items if r.code == "scrap_metal")
    assert scrap == 20 - 3  # стартовые 10 + выданные 10, списано 3

    timer = await session.scalar(
        sa.select(Timer).where(Timer.type == "craft_done", Timer.status == "pending")
    )
    assert timer is not None and timer.payload["job_id"] == str(job.id)


@requires_db
async def test_craft_missing_materials_nothing_consumed(session, seeded):
    character = await _character(session, seeded)
    await characters.remove_items(session, character, code="scrap_metal", qty=10)  # стартовый лом убран
    with pytest.raises(DomainError) as exc_info:
        await crafting.start_craft(session, character, "craft_nano_patch")
    assert exc_info.value.code == "missing_materials"
    assert "scrap_metal" in exc_info.value.message
    _, items = await characters.list_inventory(session, character.id)
    assert sum(r.qty for r in items if r.code == "scrap_metal") == 0  # ничего не списано (ТЗ)


@requires_db
async def test_craft_requires_port_and_slot(session, seeded):
    character = await _character(session, seeded, scrap=20)
    await crafting.start_craft(session, character, "craft_nano_patch")
    with pytest.raises(DomainError) as exc_info:  # 1 слот по умолчанию
        await crafting.start_craft(session, character, "craft_nano_patch")
    assert exc_info.value.code == "no_free_slot"

    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 50
    await expeditions.launch_expedition(session, character)
    with pytest.raises(DomainError) as exc_info:
        await crafting.start_craft(session, character, "craft_nano_patch")
    assert exc_info.value.code == "not_in_port"


@requires_db
async def test_craft_completion_grants_result_via_timer(session, seeded, bus):
    character = await _character(session, seeded, scrap=10)
    job = await crafting.start_craft(session, character, "craft_nano_patch")
    await _backdate_job(session, job)
    await session.flush()

    assert await _run_due(session, bus) >= 1
    await session.refresh(job)
    assert job.status == "done"
    _, items = await characters.list_inventory(session, character.id)
    nano = sum(r.qty for r in items if r.code == "nano_patch")
    assert nano == 3 + 1  # стартовые 3 + скрафченный

    texts = [m.text for m in (await session.scalars(sa.select(Outbox))).all()]
    assert any("Крафт завершён" in t for t in texts)


@requires_db
async def test_craft_result_goes_to_waiting_when_full(session, seeded, bus):
    """Результат таймера при полном инвентаре — в «Ожидает получения», не теряется (ТЗ)."""
    character = await _character(session, seeded)
    # заполняем все 30 слотов: стартовые 3 стека + 1380 лома (40 дочерновка + 27 стеков)
    await characters.grant_items(session, character, code="scrap_metal", qty=1380, allow_loss=True)
    _, check = await characters.list_inventory(session, character.id)
    assert len(check) == 30  # инвентарь полон
    job = await crafting.start_craft(session, character, "craft_trinium_refine")  # результат: trinium_ore
    await _backdate_job(session, job)

    await _run_due(session, bus)
    await session.refresh(job)
    assert job.status == "done"

    _, waiting_items = await characters.list_inventory(session, character.id, kind="waiting")
    trinium = sum(r.qty for r in waiting_items if r.code == "trinium_ore")
    assert trinium == 1  # результат в разделе ожидания (ТЗ)

    texts = [m.text for m in (await session.scalars(sa.select(Outbox))).all()]
    assert any("Ожидает получения" in t for t in texts)


@requires_db
async def test_waiting_auto_flushes_when_space_freed(session, seeded):
    """При освобождении места предметы из waiting автоматически возвращаются (ТЗ)."""
    character = await _character(session, seeded)
    await characters.grant_items(session, character, code="scrap_metal", qty=1380, allow_loss=True)
    await characters.grant_items(session, character, code="nano_patch", qty=3, kind="waiting", allow_loss=True)
    _, ship_items = await characters.list_inventory(session, character.id)
    occupied = len(ship_items)
    assert occupied <= 30
    _, waiting_before = await characters.list_inventory(session, character.id, kind="waiting")
    assert sum(r.qty for r in waiting_before if r.code == "nano_patch") == 3

    # освобождаем слот: снимаем 1 полный стек лома
    await characters.remove_items(session, character, code="scrap_metal", qty=50)
    _, waiting_after = await characters.list_inventory(session, character.id, kind="waiting")
    _, ship_after = await characters.list_inventory(session, character.id)
    nano_ship = sum(r.qty for r in ship_after if r.code == "nano_patch")
    nano_waiting = sum(r.qty for r in waiting_after if r.code == "nano_patch")
    assert nano_ship + nano_waiting == 6  # 3 стартовых + 3 из waiting
    assert nano_ship > 3  # часть автоматически вернулась (ТЗ)


@requires_db
async def test_cancel_craft_partial_return_and_timer_cancelled(session, seeded, bus):
    character = await _character(session, seeded, scrap=10)
    job = await crafting.start_craft(session, character, "craft_repair_kit")  # 5 лома
    text = await crafting.cancel_craft(session, character, 1)
    await session.refresh(job)
    assert job.status == "cancelled"
    assert "2× scrap_metal" in text  # 50% от 5

    _, items = await characters.list_inventory(session, character.id)
    assert sum(r.qty for r in items if r.code == "scrap_metal") == 20 - 5 + 2

    # таймер отменён — даже если бы сработал, эффекта нет
    assert await _run_due(session, bus) == 0
    timer = await session.scalar(
        sa.select(Timer).where(Timer.type == "craft_done", Timer.payload["job_id"].astext == str(job.id))
    )
    assert timer is not None and timer.status == "cancelled"


@requires_db
async def test_notifications_grouped_and_important_separate(session, seeded, bus):
    """Завершения объединяются в одно уведомление; важные — отдельно (ТЗ)."""
    character = await _character(session, seeded, scrap=20)
    character.craft_slots = 2  # два параллельных заказа для проверки группировки
    nano_job = await crafting.start_craft(session, character, "craft_nano_patch")
    trinium_job = await crafting.start_craft(session, character, "craft_trinium_refine")  # rare!
    await _backdate_job(session, nano_job)
    await _backdate_job(session, trinium_job)

    await _run_due(session, bus)
    texts = [m.text for m in (await session.scalars(sa.select(Outbox))).all()]
    assert any("Крафт завершён" in t and "Нанопластырь" in t for t in texts)
    assert any("Важный результат крафта" in t and "триния" in t for t in texts)


@requires_db
async def test_warehouse_store_take_and_blocks(session, seeded):
    character = await _character(session, seeded, scrap=10)
    await crafting.store_item(session, character, "scrap_metal", 6)
    _, wh = await characters.list_inventory(session, character.id, kind="warehouse")
    assert sum(r.qty for r in wh if r.code == "scrap_metal") == 6
    _, ship = await characters.list_inventory(session, character.id)
    assert sum(r.qty for r in ship if r.code == "scrap_metal") == 14  # 20 стартовых − 6

    await crafting.take_item(session, character, "scrap_metal", 2)
    _, ship = await characters.list_inventory(session, character.id)
    assert sum(r.qty for r in ship if r.code == "scrap_metal") == 16

    # склад недоступен в экспедиции (ТЗ)
    ship_row = await session.get(Ship, character.id)
    assert ship_row is not None
    ship_row.fuel = 50
    await expeditions.launch_expedition(session, character)
    with pytest.raises(DomainError) as exc_info:
        await crafting.store_item(session, character, "scrap_metal", 1)
    assert exc_info.value.code == "not_in_port"


@requires_db
async def test_store_frees_slots_and_flushes_waiting(session, seeded):
    """Перенос на склад освобождает слоты → waiting автоматически возвращается (ТЗ)."""
    character = await _character(session, seeded)
    await characters.grant_items(session, character, code="scrap_metal", qty=1380, allow_loss=True)
    await characters.grant_items(session, character, code="nano_patch", qty=3, kind="waiting", allow_loss=True)
    _, waiting_before = await characters.list_inventory(session, character.id, kind="waiting")
    assert sum(r.qty for r in waiting_before if r.code == "nano_patch") == 3

    # склад принимает 15 стеков (warehouse_size=20)
    await crafting.store_item(session, character, "scrap_metal", 50 * 15)
    _, ship_after = await characters.list_inventory(session, character.id)
    nano_ship = sum(r.qty for r in ship_after if r.code == "nano_patch")
    assert nano_ship > 0  # освободилось место — waiting вернулась автоматически
