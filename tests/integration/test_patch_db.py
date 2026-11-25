"""«Залатать корабль»: ремонт до максимума (с учётом травм), 20с таймер, блокировки."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app import battles, characters, expeditions
from app.bus import CommandBus
from app.models import Timer
from app.models_game import Character, Ship
from app.timer_handlers import build_timer_handlers
from core.errors import DomainError
from core.hull import max_hull
from tests.conftest import requires_db


async def _character(session, seeded, fuel: int = 50) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Ремонтник{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = fuel
    await session.flush()
    return character


async def _run_due(session, bus=None) -> int:
    bus = bus or CommandBus()
    handlers = await build_timer_handlers()
    return int(await bus.run_due_timers(timer_handlers=handlers, session=session))


@requires_db
async def test_patch_restores_hull_to_cap_with_malfunctions(session, seeded, bus):
    """Формула заказчика: прочность → максимум (100% − травмы×5%)."""
    character = await _character(session, seeded)
    expedition = await expeditions.launch_expedition(session, character)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.malfunctions = 2  # максимум = 100 × 0.9 = 90
    ship.current_hull = 30
    await session.flush()

    delay = await expeditions.start_patch(session, character)
    assert delay == 20  # ship.patch_duration_sec
    await session.refresh(expedition)
    assert expedition.patch_eta is not None

    # пока ремонт идёт: go и return заблокированы
    with pytest.raises(DomainError) as exc_info:
        await expeditions.proceed(session, character, rng=__import__("random").Random(1))
    assert exc_info.value.code == "ship_repairing"
    with pytest.raises(DomainError) as exc_info:
        await expeditions.request_return(session, character)
    assert exc_info.value.code == "ship_repairing"

    # ускоряем таймер → завершение ремонта
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "ship_patch", Timer.status == "pending")
        .values(fires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    expedition.patch_eta = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await _run_due(session, bus)

    await session.refresh(ship)
    await session.refresh(expedition)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    assert cap == 90
    assert ship.current_hull == cap  # восстановлено до максимума с учётом травм
    assert expedition.patch_eta is None


@requires_db
async def test_patch_rejected_when_full_or_in_battle(session, seeded):
    character = await _character(session, seeded)
    await expeditions.launch_expedition(session, character)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.current_hull = max_hull(ship.base_max_hull, ship.malfunctions)
    await session.flush()
    with pytest.raises(DomainError) as exc_info:
        await expeditions.start_patch(session, character)
    assert exc_info.value.code == "hull_full"

    character2 = await _character(session, seeded)
    expedition2 = await expeditions.launch_expedition(session, character2)
    await battles.start_battle(
        session, character2, enemy_code="pirate_scout", expedition=expedition2
    )
    with pytest.raises(DomainError) as exc_info:
        await expeditions.start_patch(session, character2)
    assert exc_info.value.code == "battle_active"


@requires_db
async def test_patch_complete_is_idempotent(session, seeded, bus):
    character = await _character(session, seeded)
    expedition = await expeditions.launch_expedition(session, character)
    ship0 = await session.get(Ship, character.id)
    assert ship0 is not None
    ship0.current_hull = 40
    await session.flush()
    await expeditions.start_patch(session, character)
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "ship_patch", Timer.status == "pending")
        .values(fires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    expedition.patch_eta = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await _run_due(session, bus)
    ship = await session.get(Ship, character.id)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    assert ship is not None and ship.current_hull == cap

    # повторная обработка того же таймера — без эффекта (идемпотентно)
    await expeditions.patch_complete(session, {"expedition_id": str(expedition.id)})
    await session.refresh(ship)
    assert ship.current_hull == cap
