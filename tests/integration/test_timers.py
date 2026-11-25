"""Планировщик таймеров: claim due, идемпотентность, отмена."""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Timer
from tests.conftest import requires_db
from worker.main import timer_handlers_stub


async def _add_timer(
    session: AsyncSession,
    *,
    type_: str = "ping",
    fires_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "pending",
) -> Timer:
    timer = Timer(
        type=type_,
        character_id=uuid.uuid4(),
        payload=payload or {},
        fires_at=fires_at or datetime.now(UTC) - timedelta(seconds=1),
        status=status,
    )
    session.add(timer)
    await session.flush()
    return timer


@requires_db
async def test_due_timer_fires(session, bus):
    await _add_timer(session, payload={"hello": "world"})
    handlers = await timer_handlers_stub()

    processed = await bus.run_due_timers(timer_handlers=handlers, session=session)
    assert processed == 1

    rows = list((await session.scalars(select(Timer))).all())
    assert rows[0].status == "fired"


@requires_db
async def test_future_timer_not_claimed(session, bus):
    await _add_timer(session, fires_at=datetime.now(UTC) + timedelta(hours=1))
    handlers = await timer_handlers_stub()

    assert await bus.run_due_timers(timer_handlers=handlers, session=session) == 0
    [timer] = (await session.scalars(select(Timer))).all()
    assert timer.status == "pending"


@requires_db
async def test_rerun_does_not_duplicate_effect(session, bus):
    """Повторный прогон не берёт уже fired таймеры (идемпотентность — ТЗ)."""
    await _add_timer(session)
    handlers = await timer_handlers_stub()

    assert await bus.run_due_timers(timer_handlers=handlers, session=session) == 1
    assert await bus.run_due_timers(timer_handlers=handlers, session=session) == 0


@requires_db
async def test_cancel_timer(session, bus):
    timer = await _add_timer(session)
    from app import stores

    cancelled = await stores.cancel_timer(
        session, timer_type="ping", payload_id_key="job_id", payload_id="x"
    )
    assert not cancelled  # payload не совпал — таймер жив

    from sqlalchemy import update

    await session.execute(
        update(Timer).where(Timer.id == timer.id).values(payload={"job_id": "42"})
    )
    await session.flush()
    assert await stores.cancel_timer(
        session, timer_type="ping", payload_id_key="job_id", payload_id="42"
    )
    await session.refresh(timer)
    assert timer.status == "cancelled"

    handlers = await timer_handlers_stub()
    assert await bus.run_due_timers(timer_handlers=handlers, session=session) == 0
