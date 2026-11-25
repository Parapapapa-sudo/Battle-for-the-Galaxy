"""Интерфейс платёжного провайдера.

Реальный провайдер подключается реализацией этого протокола — вся остальная
логика (заказы, зачисление, возвраты, журнал) от него не зависит.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderOrder:
    """Результат создания заказа у провайдера."""

    provider_order_id: str
    pay_url: str  # куда отправить игрока для оплаты
    status: str = "created"  # created|pending


class PaymentProvider(Protocol):
    """Провайдер платежей: создание, статус, отмена, возврат, уведомления."""

    name: str

    async def create_order(
        self,
        *,
        order_id: str,
        amount_rub: int,
        description: str,
    ) -> ProviderOrder: ...

    async def get_status(self, provider_order_id: str) -> str:
        """created|pending|paid|failed|cancelled|refunded"""
        ...

    async def cancel(self, provider_order_id: str) -> None: ...

    async def refund(self, provider_order_id: str, *, amount_rub: int | None = None) -> None:
        """amount_rub=None — полный возврат."""
        ...
