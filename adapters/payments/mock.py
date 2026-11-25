"""Мок-провайдер для локальной разработки.

Эмулирует поведение реального провайдера:
- create_order → генерирует provider_order_id и pay_url на локальную страницу;
- «Оплата» происходит через страницу /payments/mock/{order_id} (кнопка «Оплатить»
  шлёт webhook, как настоящий провайдер) — либо программно через mark_paid/mark_failed;
- get_status читает статус заказа из нашей же БД (у мока нет своего хранилища);
- cancel/refund меняют статус и шлют webhook (как уведомления провайдера).

При подключении реального провайдера этот модуль не используется.
"""
import uuid
from typing import Any

import httpx

from adapters.payments.base import PaymentProvider, ProviderOrder
from app.db import session_factory


class MockProvider(PaymentProvider):
    name = "mock"

    def __init__(self, base_url: str = "http://localhost:8000", http: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=15)

    async def create_order(
        self, *, order_id: str, amount_rub: int, description: str
    ) -> ProviderOrder:
        provider_order_id = f"mock-{uuid.uuid4()}"
        return ProviderOrder(
            provider_order_id=provider_order_id,
            pay_url=f"{self._base_url}/payments/mock/{order_id}",
            status="created",
        )

    async def get_status(self, provider_order_id: str) -> str:
        from sqlalchemy import select

        from app.models_game import PaymentOrder

        async with session_factory() as session:
            order = await session.scalar(
                select(PaymentOrder).where(
                    PaymentOrder.provider == self.name,
                    PaymentOrder.provider_order_id == provider_order_id,
                )
            )
        return order.status if order is not None else "unknown"

    async def cancel(self, provider_order_id: str) -> None:
        await self._set_status(provider_order_id, "cancelled")

    async def refund(self, provider_order_id: str, *, amount_rub: int | None = None) -> None:
        await self._set_status(provider_order_id, "refunded")

    # --- эмуляция действий игрока/провайдера (локальный dev) ---------------------

    async def mark_paid(self, order_id: str) -> None:
        """Игрок «оплатил» со страницы мока — как webhook от настоящего провайдера."""
        async with session_factory() as session:
            from sqlalchemy import select

            from app.models_game import PaymentOrder

            order = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id))
            assert order is not None
            provider_order_id = order.provider_order_id
        await self._set_status(provider_order_id, "paid")

    async def mark_failed(self, order_id: str) -> None:
        async with session_factory() as session:
            from sqlalchemy import select

            from app.models_game import PaymentOrder

            order = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id))
            assert order is not None
            provider_order_id = order.provider_order_id
        await self._set_status(provider_order_id, "failed")

    async def _set_status(self, provider_order_id: str, status: str) -> None:
        """Меняет статус и доставляет webhook (уведомление провайдера — ТЗ)."""
        await self._http.post(
            f"{self._base_url}/payments/webhook",
            json={
                "provider": self.name,
                "provider_order_id": provider_order_id,
                "status": status,
            },
        )


def provider_from_settings(settings: Any) -> PaymentProvider:
    """Фабрика провайдера: сейчас мок; с реальным API — выбор по настройке."""
    return MockProvider(base_url=settings.payments_base_url)
