"""Worker: лидерство через advisory lock + циклы таймеров и outbox."""
import asyncio
import contextlib
import signal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db
from app.bus import CommandBus
from app.config import Settings
from worker.dispatcher import OutboxDispatcher, Sender

log = structlog.get_logger(__name__)


async def _settle_eon_market(sessions: async_sessionmaker[AsyncSession]) -> None:
    from app.db import transactional
    from app.market import settle_eon_market

    async with sessions() as session, transactional(session):
        deals = await settle_eon_market(session)
        if deals:
            log.info("eon_market_settled", deals=deals)


async def _sync_payments(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    from adapters.payments.mock import provider_from_settings
    from app.db import transactional
    from app.payments import sync_pending_orders

    async with sessions() as session, transactional(session):
        synced = await sync_pending_orders(session, provider_from_settings(settings))
        if synced:
            log.info("payments_synced", count=synced)


async def _health_tick(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Плановая проверка работоспособности (расписание из админ-панели)."""
    from app.db import transactional
    from app.health import scheduled_tick

    async with sessions() as session, transactional(session):
        if await scheduled_tick(session, settings):
            log.info("health_check_done")


async def run_worker(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    dispatcher: OutboxDispatcher,
) -> None:
    bus = CommandBus(sessions)
    from app.timer_handlers import build_timer_handlers

    handlers = await build_timer_handlers()
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
                    await _sync_payments(sessions, settings)
                    await _health_tick(sessions, settings)
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


class _PrintSender:
    async def send(self, *, character_id: str, text: str, platform: str | None) -> None:
        log.info("outbox_message", to=character_id, platform=platform, text=text)


def _sender_from_settings(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> "Sender":
    """Транспорт outbox для worker'а.

    VK настроен — worker обязан доставлять по-настоящему (VkSender), как бот:
    claim в outbox атомарен (SKIP LOCKED), поэтому кто из двух процессов забрал
    сообщение, тот и отправляет. PrintSender здесь «съедал» бы сообщения,
    помечая их отправленными, — игроки не получали уведомления (ремонт и т.п.).
    """
    if settings.vk_bot_token and settings.vk_group_id:
        from adapters.vk.api import VkApi, VkSender

        api = VkApi(settings.vk_bot_token, api_version=settings.vk_api_version)
        return VkSender(sessions, api)
    return _PrintSender()


def main() -> None:
    import asyncio

    from app.config import get_settings
    from app.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)

    from worker.dispatcher import dispatcher_from_settings

    dispatcher = dispatcher_from_settings(
        db.session_factory, _sender_from_settings(settings, db.session_factory), settings
    )
    asyncio.run(run_worker(settings, db.session_factory, dispatcher))


if __name__ == "__main__":
    main()
