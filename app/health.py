"""Инструменты админ-панели: проверка работоспособности VK / Telegram / сервера.

Проверки лёгкие: один HTTP-запрос (VK/TG) или пара SQL (сервер). Результаты и
расписание — в config_params (`tools.health_interval_sec`, 0 = выкл, и
`tools.health_last`). Плановые проверки выполняет worker-лидер на своём цикле.
"""
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.config import Settings
from app.models import ConfigParam
from core.errors import DomainError

INTERVAL_KEY = "tools.health_interval_sec"
LAST_KEY = "tools.health_last"
TARGETS = ("vk", "tg", "server")
MIN_INTERVAL_SEC = 30  # чаще нет смысла: проверки внешних API
_MSK = timedelta(hours=3)

Checker = Callable[[], Awaitable[dict[str, Any]]]


async def _set_param(session: AsyncSession, key: str, value: Any, updated_by: str) -> None:
    param = await session.get(ConfigParam, key)
    if param is None:
        session.add(ConfigParam(key=key, value=value, updated_by=updated_by))
    else:
        param.value = value
        param.updated_by = updated_by
        param.version += 1
    await session.flush()


# --- сами проверки -------------------------------------------------------------


async def check_vk(settings: Settings) -> dict[str, Any]:
    """VK: один лёгкий вызов API с токеном бота — проверяет и сеть, и токен."""
    if not settings.vk_bot_token:
        return {"ok": False, "latency_ms": None, "detail": "VK_BOT_TOKEN не задан"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.vk.com/method/users.get",
                params={"access_token": settings.vk_bot_token, "v": settings.vk_api_version},
            )
        latency = int((time.monotonic() - started) * 1000)
        data = resp.json()
        if "error" in data:
            return {
                "ok": False,
                "latency_ms": latency,
                "detail": "VK API: " + str(data["error"].get("error_msg", "?"))[:100],
            }
        return {"ok": True, "latency_ms": latency, "detail": "API отвечает, токен валиден"}
    except Exception as exc:  # noqa: BLE001 — любой сбой сети = «не работает»
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": (str(exc) or type(exc).__name__)[:120],
        }


async def check_tg(settings: Settings) -> dict[str, Any]:
    """Telegram: getMe с токеном, если он задан; иначе — доступность api.telegram.org."""
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if settings.tg_bot_token:
                resp = await client.get(
                    f"https://api.telegram.org/bot{settings.tg_bot_token}/getMe"
                )
            else:
                resp = await client.get("https://api.telegram.org")
        latency = int((time.monotonic() - started) * 1000)
        if settings.tg_bot_token:
            data = resp.json()
            if data.get("ok"):
                return {"ok": True, "latency_ms": latency, "detail": "API отвечает, токен валиден"}
            return {
                "ok": False,
                "latency_ms": latency,
                "detail": "TG API: " + str(data.get("description", "?"))[:100],
            }
        ok = resp.status_code < 500
        return {
            "ok": ok,
            "latency_ms": latency,
            "detail": f"HTTP {resp.status_code} (токен TG не задан — проверена только доступность)",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": (str(exc) or type(exc).__name__)[:120],
        }


async def check_server(session: AsyncSession) -> dict[str, Any]:
    """Сервер: БД (SELECT 1 + латентность) и живость процессов по advisory-локам.

    Бот и worker держат сессионные pg-блокировки (db.LeaderLock) — их наличие
    в pg_locks означает, что процесс жив; это бесплатно и не требует портов.
    """
    from app.db import LeaderLock

    started = time.monotonic()
    try:
        await session.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "detail": f"БД недоступна: {str(exc)[:100]}"}
    db_latency = int((time.monotonic() - started) * 1000)
    held = set(
        (
            await session.scalars(
                sa.text(
                    "SELECT objid FROM pg_locks "
                    "WHERE locktype = 'advisory' AND classid = 0 AND objid IN (:worker, :bot) "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                ).bindparams(worker=LeaderLock.KEY, bot=LeaderLock.KEY_VK_BOT)
            )
        ).all()
    )
    worker_alive = LeaderLock.KEY in held
    bot_alive = LeaderLock.KEY_VK_BOT in held
    ok = bot_alive and worker_alive
    detail = (
        f"БД: {db_latency} мс; бот: {'работает' if bot_alive else 'НЕ работает'}; "
        f"worker: {'работает' if worker_alive else 'НЕ работает'}"
    )
    return {"ok": ok, "latency_ms": db_latency, "detail": detail}


def _default_checkers(session: AsyncSession, settings: Settings) -> dict[str, Checker]:
    return {
        "vk": lambda: check_vk(settings),
        "tg": lambda: check_tg(settings),
        "server": lambda: check_server(session),
    }


# --- запуск, хранение, расписание -------------------------------------------------


def _with_msk(stored: dict[str, Any]) -> dict[str, Any]:
    checked = datetime.fromisoformat(str(stored["checked_at"]))
    return {**stored, "checked_at_msk": (checked + _MSK).strftime("%d.%m.%Y %H:%M:%S")}


async def _execute(
    session: AsyncSession,
    settings: Settings,
    targets: tuple[str, ...],
    checkers: dict[str, Checker] | None = None,
) -> dict[str, Any]:
    """Прогон проверок + сохранение (частичный прогон мержится с прошлым)."""
    checkers = checkers or _default_checkers(session, settings)
    previous = await stores.get_param(session, LAST_KEY) or {}
    results: dict[str, Any] = dict(previous.get("results", {}))
    for target in targets:
        results[target] = await checkers[target]()
    stored = {
        "checked_at": datetime.now(UTC).isoformat(),
        "results": results,
    }
    await _set_param(session, LAST_KEY, stored, updated_by="health-check")
    return stored


async def run_checks(
    session: AsyncSession,
    admin: Any,
    settings: Settings,
    *,
    target: str = "all",
    checkers: dict[str, Checker] | None = None,
) -> dict[str, Any]:
    """Ручная проверка из админ-панели (кнопки «Проверить …»)."""
    from app import admin as admin_svc

    admin_svc.require_role(admin, "admin")
    if target != "all" and target not in TARGETS:
        raise DomainError("bad_target", f"Цель проверки: all|{'|'.join(TARGETS)}.")
    targets = TARGETS if target == "all" else (target,)
    stored = await _execute(session, settings, targets, checkers)
    await admin_svc.audit(
        session, admin, "health_check", target_type="health", target_id=target,
        after={"results": {k: v.get("ok") for k, v in stored["results"].items()}},
    )
    return await get_status(session, admin)


async def get_status(session: AsyncSession, admin: Any) -> dict[str, Any]:
    """Состояние для UI: расписание + результат последней проверки (время МСК)."""
    from app import admin as admin_svc

    admin_svc.require_role(admin, "admin")
    interval = int(await stores.get_param(session, INTERVAL_KEY, 0) or 0)
    last = await stores.get_param(session, LAST_KEY)
    return {
        "interval_sec": interval,
        "targets": list(TARGETS),
        "last": _with_msk(last) if last else None,
    }


async def set_interval(
    session: AsyncSession, admin: Any, interval_sec: int
) -> dict[str, Any]:
    """Расписание: 0 — выключить; иначе не чаще MIN_INTERVAL_SEC."""
    from app import admin as admin_svc

    admin_svc.require_role(admin, "admin")
    if interval_sec != 0 and interval_sec < MIN_INTERVAL_SEC:
        raise DomainError(
            "bad_interval", f"Интервал: 0 (выкл) или не меньше {MIN_INTERVAL_SEC} секунд."
        )
    await _set_param(session, INTERVAL_KEY, interval_sec, updated_by=admin.login)
    await admin_svc.audit(
        session, admin, "set_health_schedule", target_type="health",
        after={"interval_sec": interval_sec},
    )
    return await get_status(session, admin)


async def scheduled_tick(
    session: AsyncSession,
    settings: Settings,
    *,
    checkers: dict[str, Checker] | None = None,
) -> bool:
    """Плановая проверка из цикла worker'а. True — проверка выполнена."""
    interval = int(await stores.get_param(session, INTERVAL_KEY, 0) or 0)
    if interval <= 0:
        return False
    last = await stores.get_param(session, LAST_KEY)
    if last:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(str(last["checked_at"]))).total_seconds()
        if elapsed < interval:
            return False
    await _execute(session, settings, TARGETS, checkers)
    return True
