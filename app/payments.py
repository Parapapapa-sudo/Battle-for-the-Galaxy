"""Платёжный сервис: заказы, webhook, статусы, возвраты, отмены, журнал (ТЗ-провайдер).

Контракт (провайдер-агностичен):
- создание заказа фиксирует amount_rub и eons (1 Эон = `eon.rub_rate` руб, по умолчанию 1);
- webhook идемпотентен: UNIQUE(provider, provider_order_id) + проверка переходов;
- каждый переход состояния и движение средств — в append-only `payment_transactions`;
- отмена — до оплаты; возврат — после оплаты, с изъятием Эонов (ждём пополнения
  баланса, если Эоны уже потрачены).
"""
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import economy, stores
from app.models_game import Character, PaymentOrder, PaymentTransaction, ShopOffer
from core.errors import DomainError

TERMINAL = {"paid", "failed", "cancelled", "refunded"}
ALLOWED_TRANSITIONS = {
    "created": {"pending", "paid", "failed", "cancelled"},
    "pending": {"paid", "failed", "cancelled"},
    "paid": {"refunded", "refund_pending_balance"},
    "refund_pending_balance": {"refunded", "paid"},
}


async def _journal(
    session: AsyncSession,
    order: PaymentOrder,
    type_: str,
    *,
    actor: str = "system",
    amount_rub: int | None = None,
    eons: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        PaymentTransaction(
            order_id=order.id,
            type=type_,
            amount_rub=amount_rub,
            eons=eons,
            actor=actor,
            payload=payload,
        )
    )


# --- создание заказа -----------------------------------------------------------------


async def create_order(
    session: AsyncSession,
    character: Character,
    *,
    offer_code: str,
    provider: Any,  # PaymentProvider
    idempotency_key: str | None = None,
) -> PaymentOrder:
    """Создание заказа: сумма (₽) и Эоны фиксируются здесь и не меняются (ТЗ)."""
    if idempotency_key:
        existing = await session.scalar(
            sa.select(PaymentOrder).where(PaymentOrder.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing  # повтор создания — тот же заказ, эффектов нет

    offer = await session.scalar(
        sa.select(ShopOffer).where(
            ShopOffer.code == offer_code, ShopOffer.status == "published"
        )
    )
    if offer is None or offer.kind != "pack" or not offer.price_rub:
        raise DomainError("unknown_offer", f"Пак «{offer_code}» недоступен для покупки.")

    rate = int(await stores.get_param(session, "eon.rub_rate", 1))
    eons = offer.price_rub // rate  # фиксация на момент создания
    order = PaymentOrder(
        character_id=character.id,
        offer_id=offer.id,
        provider=provider.name,
        provider_order_id="pending",
        amount_rub=offer.price_rub,
        eons=eons,
        status="created",
        idempotency_key=idempotency_key,
    )
    session.add(order)
    await session.flush()  # нужен id для pay_url

    created = await provider.create_order(
        order_id=str(order.id),
        amount_rub=order.amount_rub,
        description=f"Battle for the Galaxy: {offer.code}",
    )
    order.provider_order_id = created.provider_order_id
    order.pay_url = created.pay_url
    if created.status != "created":
        order.status = created.status
    await _journal(
        session, order, "order_created", actor="user",
        amount_rub=order.amount_rub, eons=order.eons,
        payload={"offer": offer.code, "provider_order_id": created.provider_order_id},
    )
    await session.flush()
    return order


# --- webhook и статусы ----------------------------------------------------------------


async def handle_webhook(
    session: AsyncSession,
    *,
    provider_name: str,
    provider_order_id: str,
    status: str,
) -> PaymentOrder:
    """Обработка уведомления провайдера. Идемпотентна: повтор не двигает деньги (ТЗ)."""
    order = await session.scalar(
        sa.select(PaymentOrder).where(
            PaymentOrder.provider == provider_name,
            PaymentOrder.provider_order_id == provider_order_id,
        )
    )
    if order is None:
        raise DomainError("unknown_order", f"Заказ {provider_order_id} не найден.")

    if status == order.status or status not in ALLOWED_TRANSITIONS.get(order.status, set()):
        return order  # повтор или недопустимый переход — без эффектов

    character = await session.get(Character, order.character_id)
    assert character is not None

    if status == "paid":
        order.status = "paid"
        order.paid_at = datetime.now(UTC)
        await _journal(session, order, "payment_confirmed", actor="provider",
                       amount_rub=order.amount_rub, eons=order.eons)
        await economy.credit(
            session, character, economy.EON, order.eons,
            reason="payment", ref_type="payment_order", ref_id=str(order.id),
        )
        await _journal(session, order, "eons_granted", actor="system", eons=order.eons)
        await stores.add_outbox(
            session, character_id=character.id,
            text=f"Оплата получена: +{order.eons} Эонов (заказ {str(order.id)[:8]}).",
        )
    elif status == "failed":
        order.status = "failed"
        await _journal(session, order, "payment_failed", actor="provider",
                       amount_rub=order.amount_rub)
    elif status == "cancelled":
        order.status = "cancelled"
        order.cancelled_at = datetime.now(UTC)
        await _journal(session, order, "cancel_confirmed", actor="provider",
                       amount_rub=order.amount_rub)
    elif status == "refunded":
        await _apply_refund(session, order, character)

    await session.flush()
    return order


async def _apply_refund(session: AsyncSession, order: PaymentOrder, character: Character) -> None:
    """Возврат: изымаем Эоны. Если уже потрачены — ждём пополнения (refund_pending_balance)."""
    wallet = await economy.get_wallet(session, character.id, economy.EON)
    if wallet.balance < order.eons:
        order.status = "refund_pending_balance"
        await _journal(
            session, order, "refund_pending_balance", actor="provider",
            amount_rub=order.amount_rub, eons=order.eons,
            payload={"balance": wallet.balance},
        )
        return
    await economy.debit(
        session, character, economy.EON, order.eons,
        reason="payment_refund", ref_type="payment_order", ref_id=str(order.id),
    )
    order.status = "refunded"
    order.refunded_at = datetime.now(UTC)
    await _journal(session, order, "eons_clawed_back", actor="system", eons=order.eons)
    await _journal(session, order, "refund_confirmed", actor="provider",
                   amount_rub=order.amount_rub, eons=order.eons)
    await stores.add_outbox(
        session, character_id=character.id,
        text=f"Возврат по заказу {str(order.id)[:8]}: −{order.eons} Эонов.",
    )


async def sync_pending_orders(session: AsyncSession, provider: Any) -> int:
    """Опрос статусов незавершённых заказов (подстраховка webhook'ов, ТЗ: получение статуса)."""
    orders = (
        await session.scalars(
            sa.select(PaymentOrder).where(
                PaymentOrder.provider == provider.name,
                PaymentOrder.status.in_(["created", "pending"]),
            )
        )
    ).all()
    synced = 0
    for order in orders:
        status = await provider.get_status(order.provider_order_id)
        if status != order.status and status in ALLOWED_TRANSITIONS.get(order.status, set()):
            await handle_webhook(
                session,
                provider_name=provider.name,
                provider_order_id=order.provider_order_id,
                status=status,
            )
            synced += 1
    await session.flush()
    return synced


async def retry_refund(session: AsyncSession, order_id: uuid.UUID) -> str:
    """Повтор возврата, когда игрок пополнил баланс (из refund_pending_balance)."""
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.status != "refund_pending_balance":
        raise DomainError("no_refund_pending", "Нет ожидающего возврата.")
    character = await session.get(Character, order.character_id)
    assert character is not None
    await _apply_refund(session, order, character)
    await session.flush()
    return order.status


# --- действия пользователя/админа -------------------------------------------------------


async def cancel_order(
    session: AsyncSession, character: Character, order_id: uuid.UUID, provider: Any
) -> PaymentOrder:
    """Отмена неоплаченного заказа (ТЗ)."""
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.character_id != character.id:
        raise DomainError("no_order", "Заказ не найден.")
    if order.status not in ("created", "pending"):
        raise DomainError("cannot_cancel", f"Отмена невозможна в статусе «{order.status}».")
    await _journal(session, order, "cancel_initiated", actor="user",
                   amount_rub=order.amount_rub)
    await provider.cancel(order.provider_order_id)  # провайдер шлёт webhook → cancel_confirmed
    await session.flush()
    order = await session.get(PaymentOrder, order_id)
    assert order is not None
    return order


async def refund_order(
    session: AsyncSession, admin_login: str, order_id: uuid.UUID, provider: Any
) -> PaymentOrder:
    """Возврат оплаченного заказа (ТЗ). Запускается администратором."""
    order = await session.get(PaymentOrder, order_id)
    if order is None:
        raise DomainError("no_order", "Заказ не найден.")
    if order.status != "paid":
        raise DomainError("cannot_refund", f"Возврат невозможен в статусе «{order.status}».")
    await _journal(session, order, "refund_initiated", actor="admin",
                   amount_rub=order.amount_rub, payload={"admin": admin_login})
    await provider.refund(order.provider_order_id)  # webhook → refunded / refund_pending_balance
    await session.flush()
    order = await session.get(PaymentOrder, order_id)
    assert order is not None
    return order


async def order_status(session: AsyncSession, character: Character, order_id: uuid.UUID) -> dict[str, Any]:
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.character_id != character.id:
        raise DomainError("no_order", "Заказ не найден.")
    return {
        "id": str(order.id),
        "status": order.status,
        "amount_rub": order.amount_rub,
        "eons": order.eons,
        "pay_url": order.pay_url,
        "created_at": str(order.created_at),
        "paid_at": str(order.paid_at) if order.paid_at else None,
    }
