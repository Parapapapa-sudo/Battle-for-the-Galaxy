"""Тесты. Интеграционные требуют PostgreSQL: переменная TEST_DATABASE_URL,
например postgresql+asyncpg://postgres:postgres@localhost:5432/space_mmo_test.

Перед прогоном интеграционных тестов схема накатывается Alembic (upgrade head).
"""
import os
import subprocess
import sys

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# До импорта app.config фиксируем URL тестовой БД (настройки читаются один раз).
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["DB_POOL"] = "null"  # соединение на каждый event loop (function-scope)

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан — интеграционные тесты пропущены"
)


@pytest.fixture(scope="session")
def _migrated():
    if not TEST_DATABASE_URL:
        yield
        return
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        check=True,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        capture_output=True,
    )
    yield


@pytest.fixture
async def session(_migrated):
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import engine

    async with engine.connect() as conn:
        trans = await conn.begin()
        sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield sess
        finally:
            await trans.rollback()
            await conn.close()


@pytest.fixture
async def bus():
    """Bus без фабрики: сессия внедряется в каждый вызов (rollback снаружи)."""
    from app.bus import CommandBus

    return CommandBus()


@pytest.fixture
async def seeded(session):
    """Контент-заглушки (фракции, шаблоны) внутри транзакции теста."""
    from app.seeds import ensure_seeds

    await ensure_seeds(session)
    return True


@pytest.fixture
async def sessions(session):
    """Фабрика сессий поверх транзакции теста (для роутера/диспетчеров)."""

    class _Ctx:
        def __init__(self, sess: AsyncSession) -> None:
            self._sess = sess

        async def __aenter__(self) -> AsyncSession:
            return self._sess

        async def __aexit__(self, *exc: object) -> bool:
            return False

    class _Factory:
        def __call__(self) -> "_Ctx":
            return _Ctx(session)

    return _Factory()
