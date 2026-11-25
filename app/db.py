"""БД-слой: движок, сессии, advisory locks.

Пер-character последовательность команд — через pg_try_advisory_xact_lock:
блокировка держится до конца транзакции и снимается автоматически при
rollback/commit (нет «зависших» блокировок при падении процесса).

DB_POOL=null — NullPool (тесты: свежее соединение на текущий event loop).
"""
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool if os.environ.get("DB_POOL") == "null" else None,
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transactional(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Транзакция для use case: снаружи уже есть — не трогаем; нет — begin/commit."""
    if session.in_transaction():
        yield session
    else:
        async with session.begin():
            yield session


async def try_advisory_lock(conn: AsyncConnection, key: str) -> bool:
    """True — блокировка получена (в рамках текущей транзакции)."""
    row = await conn.execute(
        sa.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )
    return bool(row.scalar_one())


class LeaderLock:
    """Сессионная блокировка лидера worker'а (таймеры/диспетчер запускаются в одном экземпляре)."""

    KEY = 727_000

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def acquire(self) -> bool:
        row = await self._conn.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": self.KEY}
        )
        return bool(row.scalar_one())

    async def release(self) -> None:
        await self._conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": self.KEY})


async def health_check() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:
        return False
