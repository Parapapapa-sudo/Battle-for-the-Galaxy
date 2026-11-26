"""Admin API (этап 9): RBAC, модерация, конфигурация, контент, аналитика.

Аутентификация: заголовок X-Admin-Key (sha256 в БД). Все действия — в audit_log.
"""
import uuid
from collections.abc import AsyncIterator
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin, stores
from app.db import session_factory, transactional
from app.models_game import AdminUser
from core.errors import DomainError

router = APIRouter(prefix="/admin", tags=["admin"])


async def _session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def current_admin(  # noqa: B008 — Depends в аргументах (идиома FastAPI)
    x_admin_key: str = Header(default=""), session: AsyncSession = Depends(_session)
) -> AdminUser:
    if not x_admin_key:
        raise HTTPException(status_code=401, detail="X-Admin-Key required")
    try:
        return await admin.authenticate(session, x_admin_key)
    except DomainError as exc:
        raise HTTPException(status_code=401, detail=exc.message) from exc


def _guard(exc: DomainError) -> NoReturn:
    status = 403 if exc.code == "forbidden" else 400
    raise HTTPException(status_code=status, detail=exc.message) from exc


@router.get("/config/{key}")
async def get_config(key: str, adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            admin.require_role(adm, "admin")
            return {"key": key, "value": await stores.get_param(session, key)}
        except DomainError as exc:
            _guard(exc)


@router.put("/config/{key}")
async def put_config(
    key: str, body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.set_config(session, adm, key, body.get("value"))
        except DomainError as exc:
            _guard(exc)


@router.post("/accounts/{account_id}/ban")
async def ban_account(
    account_id: uuid.UUID,
    body: dict[str, Any],
    adm: AdminUser = Depends(current_admin),
) -> dict[str, str]:
    async with session_factory() as session, transactional(session):
        try:
            await admin.ban_account(
                session, adm, account_id, reason=str(body.get("reason", "нарушение")),
                hours=body.get("hours"),
            )
            return {"status": "banned"}
        except DomainError as exc:
            _guard(exc)


@router.post("/accounts/{account_id}/unban")
async def unban_account(
    account_id: uuid.UUID, adm: AdminUser = Depends(current_admin)
) -> dict[str, str]:
    async with session_factory() as session, transactional(session):
        try:
            await admin.unban_account(session, adm, account_id)
            return {"status": "active"}
        except DomainError as exc:
            _guard(exc)


@router.post("/characters/{character_id}/effects")
async def apply_effect(
    character_id: uuid.UUID,
    body: dict[str, Any],
    adm: AdminUser = Depends(current_admin),
) -> dict[str, str]:
    async with session_factory() as session, transactional(session):
        try:
            await admin.apply_effect(
                session, adm, character_id, str(body["code"]),
                params=body.get("params"), duration_sec=body.get("duration_sec"),
            )
            return {"status": "applied"}
        except DomainError as exc:
            _guard(exc)


@router.get("/characters/{character_id}/history")
async def character_history(
    character_id: uuid.UUID, adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.character_history(session, character_id)
        except DomainError as exc:
            _guard(exc)


@router.get("/content")
async def content_list(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.list_content(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.post("/content/{kind}")
async def content_create(
    kind: str, body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.create_content(session, adm, kind, body)
        except DomainError as exc:
            _guard(exc)


@router.put("/content/{kind}/{code}")
async def content_update(
    kind: str, code: str, body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.update_content(session, adm, kind, code, body)
        except DomainError as exc:
            _guard(exc)


@router.delete("/content/{kind}/{code}")
async def content_delete(
    kind: str, code: str, adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.delete_content(session, adm, kind, code)
        except DomainError as exc:
            _guard(exc)


@router.post("/content/{table}/{code}/status")
async def set_content_status(
    table: str, code: str, body: dict[str, Any],
    adm: AdminUser = Depends(current_admin),
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.set_content_status(
                session, adm, table, code, str(body.get("status", "published"))
            )
        except DomainError as exc:
            _guard(exc)


@router.get("/items")
async def items_list(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.list_items(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.post("/items")
async def item_create(
    body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.create_item(session, adm, body)
        except DomainError as exc:
            _guard(exc)


@router.put("/items/{code}")
async def item_update(
    code: str, body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.update_item(session, adm, code, body)
        except DomainError as exc:
            _guard(exc)


@router.get("/analytics")
async def analytics(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.analytics(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.get("/server-load")
async def server_load(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.server_load(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.get("/audit-log")
async def audit_log(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await admin.audit_log_view(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.get("/health")
async def health_status(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    from app import health

    async with session_factory() as session, transactional(session):
        try:
            return await health.get_status(session, adm)
        except DomainError as exc:
            _guard(exc)


@router.post("/health/check")
async def health_check(
    body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    from app import health
    from app.config import get_settings

    async with session_factory() as session, transactional(session):
        try:
            return await health.run_checks(
                session, adm, get_settings(), target=str(body.get("target", "all"))
            )
        except DomainError as exc:
            _guard(exc)


@router.put("/health/schedule")
async def health_schedule(
    body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    from app import health

    async with session_factory() as session, transactional(session):
        try:
            return await health.set_interval(session, adm, int(body.get("interval_sec", 0)))
        except (DomainError, TypeError, ValueError) as exc:
            if isinstance(exc, DomainError):
                _guard(exc)
            raise HTTPException(status_code=400, detail="interval_sec: целое число") from exc


@router.post("/admins")
async def create_admin(
    body: dict[str, Any], adm: AdminUser = Depends(current_admin)
) -> dict[str, str]:
    """Создать администратора/модератора/контент-менеджера. Ключ показывается один раз."""
    async with session_factory() as session, transactional(session):
        try:
            api_key = await admin.create_admin(
                session, adm, login=str(body["login"]), role=str(body["role"])
            )
            return {"login": str(body["login"]), "api_key": api_key}
        except DomainError as exc:
            _guard(exc)


@router.get("/whoami")
async def whoami(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    return await admin.whoami(adm)


@router.get("/lookup")
async def lookup(
    name: str, adm: AdminUser = Depends(current_admin)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return {"results": await admin.lookup_character(session, adm, name)}
        except DomainError as exc:
            _guard(exc)


@router.get("/effects")
async def effects(adm: AdminUser = Depends(current_admin)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return {"effects": await admin.list_effects(session, adm)}
        except DomainError as exc:
            _guard(exc)
