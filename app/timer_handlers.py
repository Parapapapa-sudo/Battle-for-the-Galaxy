"""Реестр обработчиков таймеров: общий для worker и команды sync (анти-застревание).

Локально worker может быть не запущен — команда sync обрабатывает просроченные
таймеры самого игрока, поэтому прогресс никогда не «зависает» без worker'а.
"""
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores

log = structlog.get_logger(__name__)

TimerHandlers = dict[str, stores.TimerHandler]


async def build_timer_handlers() -> TimerHandlers:
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


async def sync_character_timers(session: AsyncSession, character_id: Any) -> list[str]:
    """Обработать просроченные таймеры игрока (идемпотентно). Возвращает типы сработавших."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from app.models import Timer

    due = (
        await session.scalars(
            sa.select(Timer)
            .where(
                Timer.character_id == character_id,
                Timer.status == "pending",
                Timer.fires_at <= datetime.now(UTC),
            )
            .order_by(Timer.fires_at)
            .with_for_update()
        )
    ).all()
    if not due:
        return []
    handlers = await build_timer_handlers()
    fired: list[str] = []
    for timer in due:
        timer.status = "fired"
        handler = handlers.get(timer.type)
        if handler is None:
            log.warning("timer_no_handler", type=timer.type, id=str(timer.id))
            continue
        await handler(session, timer.payload or {})
        fired.append(timer.type)
    await session.flush()
    return fired
