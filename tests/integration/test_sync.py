"""Анти-застревание: sync обрабатывает просроченные таймеры без worker'а."""
import uuid

import sqlalchemy as sa

from app import characters, expeditions
from app.bus import CommandBus
from app.models_game import Character, Ship
from app.router import route_text_command
from app.timer_handlers import sync_character_timers
from tests.conftest import requires_db


async def _character(session, seeded, fuel: int = 50) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Синхрон{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = fuel
    await session.flush()
    return character


@requires_db
async def test_sync_completes_stuck_return(session, seeded):
    """Возвращение без worker'а: sync доводит до прибытия (корабль в порту)."""
    character = await _character(session, seeded)
    expedition = await expeditions.launch_expedition(session, character)
    await expeditions.request_return(session, character)
    await session.refresh(expedition)
    assert expedition.status == "returning"

    # таймер прибытия «просрочен» (worker не бежал)
    from datetime import UTC, datetime, timedelta

    from app.models import Timer

    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "exp_return_arrival", Timer.status == "pending")
        .values(fires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.flush()

    fired = await sync_character_timers(session, character.id)
    assert "exp_return_arrival" in fired
    await session.refresh(expedition)
    assert expedition.status == "completed"
    ship = await session.get(Ship, character.id)
    assert ship is not None and ship.status == "dock"


@requires_db
async def test_sync_command_via_router(session, seeded, sessions):
    character = await _character(session, seeded)
    await expeditions.launch_expedition(session, character)
    await expeditions.request_return(session, character)

    from datetime import UTC, datetime, timedelta

    from app.models import Timer

    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "exp_return_arrival", Timer.status == "pending")
        .values(fires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.flush()

    bus = CommandBus()
    account = await session.get(__import__("app.models_game", fromlist=["Account"]).Account, character.account_id)
    identity = (
        await session.scalars(
            sa.select(__import__("app.models_game", fromlist=["AccountIdentity"]).AccountIdentity).where(
                __import__("app.models_game", fromlist=["AccountIdentity"]).AccountIdentity.account_id == account.id
            )
        )
    ).first()
    assert identity is not None

    result = await route_text_command(
        bus, sessions,
        platform="console", platform_user_id=identity.platform_user_id,
        text="sync", idempotency_key=f"sync-{uuid.uuid4()}",
    )
    # роутер сам догоняет просроченные таймеры перед командой (auto-sync) —
    # поэтому sync либо отчитался о сработавшем таймере, либо видит чистоту
    assert "exp_return_arrival" in result.message or "норме" in result.message

    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    assert expedition is None  # завершилась (auto-sync или sync)


@requires_db
async def test_sync_no_pending(session, seeded):
    character = await _character(session, seeded)
    fired = await sync_character_timers(session, character.id)
    assert fired == []
