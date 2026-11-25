"""Worker: лидерство через advisory lock + циклы таймеров и outbox."""
import asyncio
import contextlib
import signal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db, stores
from app.bus import CommandBus
from app.config import Settings
from worker.dispatcher import OutboxDispatcher

log = structlog.get_logger(__name__)


async def timer_handlers_stub() -> dict[str, stores.TimerHandler]:
    """Обработчики типов таймеров (реальные типы добавляются по этапам)."""

    async def ping(session: AsyncSession, payload: stores.Params) -> None:
        log.info("timer_ping", payload=payload)

    async def exp_return_arrival(session: AsyncSession, payload: stores.Params) -> None:
        from app.expeditions import arrive_expedition

        await arrive_expedition(session, payload)

    async def craft_done(session: AsyncSession, payload: stores.Params) -> None:
        from app.crafting import craft_done_handler

        await craft_done_handler(session, payload)

    async def transfer_expiry(session: AsyncSession, payload: stores.Params) -> None:
        from app.economy import transfer_expiry_handler

        await transfer_expiry_handler(session, payload)

    async def auction_expiry(session: AsyncSession, payload: stores.Params) -> None:
        from app.market import auction_expiry_handler

        await auction_expiry_handler(session, payload)

    async def auction_return(session: AsyncSession, payload: stores.Params) -> None:
        from app.market import auction_return_handler

        await auction_return_handler(session, payload)

    async def subscription_expiry(session: AsyncSession, payload: stores.Params) -> None:
        from app.market import subscription_expiry_handler

        await subscription_expiry_handler(session, payload)

    async def faction_event_start(session: AsyncSession, payload: stores.Params) -> None:
        from app.quests import faction_event_start_handler

        await faction_event_start_handler(session, payload)

    async def faction_event_end(session: AsyncSession, payload: stores.Params) -> None:
        from app.quests import faction_event_end_handler

        await faction_event_end_handler(session, payload)

    return {
        "ping": ping,
        "exp_return_arrival": exp_return_arrival,
        "craft_done": craft_done,
        "transfer_expiry": transfer_expiry,
        "auction_expiry": auction_expiry,
        "auction_return": auction_return,
        "subscription_expiry": subscription_expiry,
        "faction_event_start": faction_event_start,
        "faction_event_end": faction_event_end,
    }


async def _settle_eon_market(sessions: async_sessionmaker[AsyncSession]) -> None:
    from app.db import transactional
    from app.market import settle_eon_market

    async with sessions() as session, transactional(session):
        deals = await settle_eon_market(session)
        if deals:
            log.info("eon_market_settled", deals=deals)


async def run_worker(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    dispatcher: OutboxDispatcher,
) -> None:
    bus = CommandBus(sessions)
    handlers = await timer_handlers_stub()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    conn = await db.engine.connect()
    leader = db.LeaderLock(conn)
    is_leader = False
    log.info("worker_started", env=settings.app_env)

    try:
        while not stop.is_set():
            if not is_leader:
                is_leader = await leader.acquire()
                if is_leader:
                    log.info("worker_leader_acquired")
            if is_leader:
                try:
                    await bus.run_due_timers(timer_handlers=handlers)
                    await _settle_eon_market(sessions)
                    await dispatcher.run_once()
                except Exception:  # noqa: BLE001 — циклы не должны умирать
                    log.exception("worker_cycle_error")
            await asyncio.sleep(settings.worker_poll_interval_sec)
    finally:
        if is_leader:
            await leader.release()
        await conn.close()
        await db.engine.dispose()
        log.info("worker_stopped")


def main() -> None:
    import asyncio

    from app.config import get_settings
    from app.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)

    class _PrintSender:
        async def send(self, *, character_id: str, text: str, platform: str | None) -> None:
            log.info("outbox_message", to=character_id, platform=platform, text=text)

    from worker.dispatcher import dispatcher_from_settings

    dispatcher = dispatcher_from_settings(db.session_factory, _PrintSender(), settings)
    asyncio.run(run_worker(settings, db.session_factory, dispatcher))


if __name__ == "__main__":
    main()
