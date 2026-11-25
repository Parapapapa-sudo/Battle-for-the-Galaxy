"""Диспетчер outbox: доставка сообщений с rate limit и экспоненциальным бэкоффом.

Гарантии ТЗ: подтверждённые операции не теряются и не дублируются —
отправка идемпотентна по id записи outbox (повторная отправка после сбоя
возможна на уровне транспорта, эффект команды при этом применён ровно один раз).
"""
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.config import Settings
from app.db import transactional

log = structlog.get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class Sender(Protocol):
    """Транспорт отправки. Реализации: консоль (dev), VK (этап 1), Telegram (позже)."""

    async def send(self, *, character_id: str, text: str, platform: str | None) -> None: ...


class OutboxDispatcher:
    def __init__(
        self,
        sessions: SessionFactory,
        sender: Sender,
        *,
        batch_size: int = 50,
        send_per_sec: float = 20.0,
        max_attempts: int = 10,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._sessions = sessions
        self._sender = sender
        self._batch_size = batch_size
        self._delay = 1.0 / send_per_sec if send_per_sec > 0 else 0.0
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay

    async def run_once(self) -> int:
        from datetime import UTC, datetime

        sent = 0
        now = datetime.now(UTC)
        async with self._sessions() as session, transactional(session):
            batch = await stores.fetch_due_outbox(session, limit=self._batch_size)
            for msg in batch:
                try:
                    await self._sender.send(
                        character_id=str(msg.character_id),
                        text=msg.text,
                        platform=msg.platform,
                    )
                    msg.status = "sent"
                    msg.sent_at = now
                    sent += 1
                    from app.metrics import OUTBOX_SENT

                    OUTBOX_SENT.inc()
                except Exception as exc:  # noqa: BLE001 — любой сбой транспорта = retry
                    from app.metrics import OUTBOX_FAILED

                    OUTBOX_FAILED.inc()
                    msg.attempts += 1
                    stores.schedule_outbox_retry(
                        msg, max_attempts=self._max_attempts, base_delay=self._retry_base_delay
                    )
                    log.warning(
                        "outbox_send_failed",
                        id=msg.id,
                        attempts=msg.attempts,
                        error=str(exc),
                    )
                if self._delay:
                    await asyncio.sleep(self._delay)
            if sent:
                log.info("outbox_sent", count=sent)
        return sent


def dispatcher_from_settings(
    sessions: SessionFactory, sender: Sender, settings: Settings
) -> OutboxDispatcher:
    return OutboxDispatcher(
        sessions,
        sender,
        batch_size=settings.outbox_batch_size,
        send_per_sec=settings.outbox_send_per_sec,
        max_attempts=settings.outbox_max_attempts,
    )
