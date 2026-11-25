"""Платёжный HTTP-слой: заказы, webhook провайдера, mock-страница оплаты, возвраты.

При подключении реального провайдера меняется только `provider_from_settings`
и payload webhook'а (контракт: provider, provider_order_id, status).
"""
import uuid
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from adapters.http.miniapp_api import current_character
from app import payments
from app.db import session_factory, transactional
from app.models_game import Character
from core.errors import DomainError

router = APIRouter(tags=["payments"])


class CreateOrderBody(BaseModel):
    offer_code: str
    idempotency_key: str | None = None


def _guard(exc: DomainError) -> NoReturn:
    raise HTTPException(status_code=400, detail=exc.message) from exc


def _provider(request: Request) -> Any:
    from adapters.payments.mock import MockProvider

    settings = request.app.state.settings
    return MockProvider(base_url=settings.payments_base_url)


# --- игрок ---------------------------------------------------------------------


@router.post("/api/payments/orders")
async def create_order(
    request: Request,
    body: CreateOrderBody,
    character: Character = Depends(current_character),
) -> dict[str, Any]:
    from adapters.payments.mock import provider_from_settings

    async with session_factory() as session, transactional(session):
        try:
            order = await payments.create_order(
                session, character,
                offer_code=body.offer_code,
                provider=provider_from_settings(request.app.state.settings),
                idempotency_key=body.idempotency_key,
            )
            return await payments.order_status(session, character, order.id)
        except DomainError as exc:
            _guard(exc)


@router.get("/api/payments/orders/{order_id}")
async def get_order(
    order_id: uuid.UUID, character: Character = Depends(current_character)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            return await payments.order_status(session, character, order_id)
        except DomainError as exc:
            _guard(exc)


@router.post("/api/payments/orders/{order_id}/cancel")
async def cancel_order(
    request: Request, order_id: uuid.UUID,
    character: Character = Depends(current_character),
) -> dict[str, Any]:
    from adapters.payments.mock import provider_from_settings

    async with session_factory() as session, transactional(session):
        try:
            await payments.cancel_order(
                session, character, order_id,
                provider=provider_from_settings(request.app.state.settings),
            )
            return await payments.order_status(session, character, order_id)
        except DomainError as exc:
            _guard(exc)


# --- webhook провайдера (идемпотентный, ТЗ) -----------------------------------------


class WebhookBody(BaseModel):
    provider: str
    provider_order_id: str
    status: str


@router.post("/payments/webhook")
async def payments_webhook(body: WebhookBody) -> dict[str, str]:
    async with session_factory() as session, transactional(session):
        try:
            await payments.handle_webhook(
                session,
                provider_name=body.provider,
                provider_order_id=body.provider_order_id,
                status=body.status,
            )
            return {"status": "ok"}
        except DomainError as exc:
            _guard(exc)


# --- mock-страница оплаты (локальный dev) --------------------------------------------


@router.get("/payments/mock/{order_id}", response_class=HTMLResponse)
async def mock_pay_page(order_id: uuid.UUID) -> str:

    from app.models_game import PaymentOrder

    async with session_factory() as session, transactional(session):
        order = await session.get(PaymentOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Оплата (мок)</title></head>
<body style="font-family:sans-serif;max-width:420px;margin:40px auto">
<h2>Мок-оплата Battle for the Galaxy</h2>
<p>Заказ <b>{str(order.id)[:8]}</b> · {order.amount_rub} ₽ → {order.eons} Эонов<br>
Статус: <b>{order.status}</b></p>
<form method="post" action="/payments/mock/{order.id}/pay">
  <button type="submit" style="font-size:16px;padding:10px 24px">Оплатить (мок)</button>
</form>
</body></html>"""


@router.post("/payments/mock/{order_id}/pay")
async def mock_pay(request: Request, order_id: uuid.UUID) -> dict[str, Any]:
    from adapters.payments.mock import MockProvider

    provider = MockProvider(base_url=request.app.state.settings.payments_base_url)
    await provider.mark_paid(str(order_id))
    return {"status": "paid", "order_id": str(order_id)}


# --- администрирование -----------------------------------------------------------------


@router.post("/admin/payments/{order_id}/refund")
async def admin_refund(
    request: Request, order_id: uuid.UUID,
    admin_login: str = "owner",  # в MVP сценарий админа; RBAC-заголовок — из admin_api
) -> dict[str, Any]:
    from adapters.payments.mock import provider_from_settings

    async with session_factory() as session, transactional(session):
        try:
            order = await payments.refund_order(
                session, admin_login, order_id,
                provider=provider_from_settings(request.app.state.settings),
            )
            return {"order_id": str(order.id), "status": order.status}
        except DomainError as exc:
            _guard(exc)


@router.post("/admin/payments/{order_id}/retry_refund")
async def admin_retry_refund(order_id: uuid.UUID) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        try:
            status = await payments.retry_refund(session, order_id)
            return {"order_id": str(order_id), "status": status}
        except DomainError as exc:
            _guard(exc)
