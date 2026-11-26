"""Инструменты админ-панели: проверки VK/TG/сервера, расписание, хранение результатов."""
import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import sqlalchemy as sa

from app import admin, health
from app.config import get_settings
from app.models import AuditLog
from app.models_game import AdminUser
from core.errors import DomainError
from tests.conftest import requires_db

OWNER_KEY = "dev-only-owner-key"


async def _admins(session) -> tuple[AdminUser, AdminUser]:
    mod = AdminUser(login="mod1", api_key_hash=admin.hash_key("mod-key"), role="moderator")
    session.add(mod)
    await session.flush()
    owner = await session.scalar(sa.select(AdminUser).where(AdminUser.login == "owner"))
    assert owner is not None
    return owner, mod


def _fakes(ok: bool = True) -> dict[str, Callable[[], Awaitable[dict[str, Any]]]]:
    async def fake() -> dict[str, Any]:
        return {"ok": ok, "latency_ms": 1, "detail": "fake"}

    return {t: fake for t in ("vk", "tg", "server")}


@requires_db
async def test_check_server_reports_db_and_processes(session, seeded):
    """Проверка сервера: БД отвечает; бот/worker без локов — «не работают»."""
    result = await health.check_server(session)
    assert result["latency_ms"] is not None
    assert "БД:" in result["detail"]
    # в тесте advisory-локи бота/worker'а не держатся — процессы «не работают»
    assert "НЕ работает" in result["detail"] and result["ok"] is False


@requires_db
async def test_run_checks_stores_and_merges_results(session, seeded):
    owner, mod = await _admins(session)
    settings = get_settings()

    status = await health.run_checks(session, owner, settings, checkers=_fakes())
    assert status["last"] is not None
    assert re.fullmatch(
        r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}", status["last"]["checked_at_msk"]
    )
    assert all(status["last"]["results"][t]["ok"] for t in ("vk", "tg", "server"))

    # частичная проверка (только vk) мержится: tg/server остаются от прошлой
    failing_vk = _fakes(ok=False)
    status = await health.run_checks(
        session, owner, settings, target="vk", checkers=failing_vk
    )
    assert status["last"]["results"]["vk"]["ok"] is False
    assert status["last"]["results"]["tg"]["ok"] is True

    # аудит ручной проверки
    actions = (
        await session.scalars(
            sa.select(AuditLog.action).where(AuditLog.action == "health_check")
        )
    ).all()
    assert len(actions) == 2

    # модератору нельзя
    with pytest.raises(DomainError) as exc_info:
        await health.run_checks(session, mod, settings, checkers=_fakes())
    assert exc_info.value.code == "forbidden"


@requires_db
async def test_schedule_validation_and_tick(session, seeded):
    owner, mod = await _admins(session)
    settings = get_settings()

    with pytest.raises(DomainError):
        await health.set_interval(session, owner, 10)  # меньше минимума
    with pytest.raises(DomainError) as exc_info:
        await health.set_interval(session, mod, 300)  # модератору нельзя
    assert exc_info.value.code == "forbidden"

    # расписание выключено — tick ничего не делает
    await health.set_interval(session, owner, 0)
    assert await health.scheduled_tick(session, settings, checkers=_fakes()) is False

    # включили — первая проверка выполняется, повторная сразу — пропускается
    status = await health.set_interval(session, owner, 300)
    assert status["interval_sec"] == 300
    assert await health.scheduled_tick(session, settings, checkers=_fakes()) is True
    assert await health.scheduled_tick(session, settings, checkers=_fakes()) is False

    last = await health.get_status(session, owner)
    assert last["last"] is not None and last["last"]["results"]["vk"]["ok"] is True
