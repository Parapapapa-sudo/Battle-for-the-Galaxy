"""Диспетчер outbox: доставка, retry с бэкоффом, перевод в failed."""
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.models import Outbox
from tests.conftest import requires_db
from worker.dispatcher import OutboxDispatcher


class _RecordingSender:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[str] = []
        self.calls = 0
        self.fail_times = fail_times

    async def send(self, *, character_id: str, text: str, platform: str | None) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("platform down")
        self.sent.append(text)


@requires_db
async def test_dispatch_sends_pending(session):
    char_id = uuid.uuid4()
    await stores.add_outbox(session, character_id=char_id, text="привет")
    await session.flush()

    sender = _RecordingSender()
    dispatcher = OutboxDispatcher(_factory(session), sender, send_per_sec=0)
    assert await dispatcher.run_once() == 1
    assert sender.sent == ["привет"]
    rows = await _pending(session)
    assert rows == []


@requires_db
async def test_failed_send_retries_with_backoff(session):
    char_id = uuid.uuid4()
    await stores.add_outbox(session, character_id=char_id, text="retry me")
    await session.flush()

    sender = _RecordingSender(fail_times=2)
    dispatcher = OutboxDispatcher(
        _factory(session), sender, send_per_sec=0, max_attempts=10, retry_base_delay=0.0
    )

    await dispatcher.run_once()  # fail #1
    [msg] = await _all_outbox(session)
    assert msg.attempts == 1
    assert msg.status == "pending"

    await dispatcher.run_once()  # fail #2
    await dispatcher.run_once()  # success
    [msg] = await _all_outbox(session)
    assert msg.status == "sent"
    assert msg.attempts == 2  # attempts — счётчик НЕУДАЧ, успех не инкрементирует


@requires_db
async def test_exhausted_attempts_marks_failed(session):
    char_id = uuid.uuid4()
    await stores.add_outbox(session, character_id=char_id, text="doomed")
    await session.flush()

    sender = _RecordingSender(fail_times=999)
    dispatcher = OutboxDispatcher(
        _factory(session), sender, send_per_sec=0, max_attempts=3, retry_base_delay=0.0
    )
    for _ in range(3):
        await dispatcher.run_once()

    [msg] = await _all_outbox(session)
    assert msg.status == "failed"
    assert msg.attempts == 3


@requires_db
async def test_future_messages_not_sent(session):
    char_id = uuid.uuid4()
    msg = Outbox(
        character_id=char_id,
        text="later",
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(msg)
    await session.flush()

    sender = _RecordingSender()
    dispatcher = OutboxDispatcher(_factory(session), sender, send_per_sec=0)
    assert await dispatcher.run_once() == 0
    assert sender.sent == []


def _factory(session: AsyncSession) -> Callable[[], "_SessionCtx"]:
    class _Sessions:
        def __call__(self) -> "_SessionCtx":
            return _SessionCtx(session)

    return _Sessions()


class _SessionCtx:
    """run_once использует `async with sessions() as s: async with s.begin()` —
    подменяем на внешнюю сессию теста (begin даёт SAVEPOINT внутри транзакции
    фикстуры; итоговый rollback делает фикстура)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def _pending(session: AsyncSession) -> list[Outbox]:
    return list((await session.scalars(select(Outbox).where(Outbox.status == "pending"))).all())


async def _all_outbox(session: AsyncSession) -> list[Outbox]:
    return list((await session.scalars(select(Outbox))).all())
