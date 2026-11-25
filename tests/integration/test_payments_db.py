"""Платёжный контур: заказы с фиксацией, webhook, идемпотентность, возвраты, журнал."""
import uuid

import pytest
import sqlalchemy as sa

from adapters.payments.base import ProviderOrder
from app import characters, economy, payments
from app.models_game import Character, PaymentOrder, PaymentTransaction, ShopOffer
from core.errors import DomainError
from tests.conftest import requires_db


class FakeProvider:
    """Провайдер без HTTP: webhook в тестах подаём через payments.handle_webhook."""

    name = "mock"

    def __init__(self) -> None:
        self.created: list[tuple[str, int]] = []
        self.cancels: list[str] = []
        self.refunds: list[str] = []
        self.status_map: dict[str, str] = {}

    async def create_order(self, *, order_id: str, amount_rub: int, description: str) -> ProviderOrder:
        pid = f"fake-{order_id[:8]}"
        self.created.append((pid, amount_rub))
        return ProviderOrder(provider_order_id=pid, pay_url=f"http://pay/{pid}", status="created")

    async def get_status(self, provider_order_id: str) -> str:
        return self.status_map.get(provider_order_id, "created")

    async def cancel(self, provider_order_id: str) -> None:
        self.cancels.append(provider_order_id)

    async def refund(self, provider_order_id: str, *, amount_rub: int | None = None) -> None:
        self.refunds.append(provider_order_id)


async def _character(session, seeded) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Плательщик{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )


async def _eons(session, character) -> int:
    wallet = await economy.get_wallet(session, character.id, economy.EON)
    return wallet.balance


async def _webhook(session, order: PaymentOrder, status: str) -> PaymentOrder:
    return await payments.handle_webhook(
        session,
        provider_name=order.provider,
        provider_order_id=order.provider_order_id,
        status=status,
    )


@requires_db
async def test_create_order_fixes_amount_and_eons(session, seeded):
    character = await _character(session, seeded)
    provider = FakeProvider()

    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    assert order.amount_rub == 100
    assert order.eons == 100  # курс 1 Эон = 1 ₽, зафиксирован при создании
    assert order.status == "created"
    assert provider.created == [(order.provider_order_id, 100)]
    offer = await session.get(ShopOffer, order.offer_id)
    offer.price_rub = 999  # меняем цену ПОСЛЕ — заказ не меняется
    await session.flush()
    assert order.amount_rub == 100 and order.eons == 100


@requires_db
async def test_create_order_idempotent_by_key(session, seeded):
    character = await _character(session, seeded)
    provider = FakeProvider()
    o1 = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider,
        idempotency_key="idem-1",
    )
    o2 = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider,
        idempotency_key="idem-1",
    )
    assert o1.id == o2.id
    assert len(provider.created) == 1


@requires_db
async def test_webhook_paid_grants_once_and_is_idempotent(session, seeded):
    """Защита от повторного начисления: повтор webhook не двигает деньги (ТЗ)."""
    character = await _character(session, seeded)
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=FakeProvider()
    )

    order = await _webhook(session, order, "paid")
    assert order.status == "paid"
    assert await _eons(session, character) == 100

    again = await _webhook(session, order, "paid")  # повторное уведомление провайдера
    assert again.status == "paid"
    assert await _eons(session, character) == 100  # не зачислено повторно

    grants = (
        await session.scalars(
            sa.select(PaymentTransaction).where(
                PaymentTransaction.order_id == order.id, PaymentTransaction.type == "eons_granted"
            )
        )
    ).all()
    assert len(grants) == 1


@requires_db
async def test_status_sync_from_provider(session, seeded):
    """Получение статуса оплаты: опрос провайдера подхватывает пропущенный webhook."""
    character = await _character(session, seeded)
    provider = FakeProvider()
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    provider.status_map[order.provider_order_id] = "paid"

    synced = await payments.sync_pending_orders(session, provider)
    assert synced == 1
    assert await _eons(session, character) == 100


@requires_db
async def test_cancel_before_payment(session, seeded):
    character = await _character(session, seeded)
    provider = FakeProvider()
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    await payments.cancel_order(session, character, order.id, provider)
    assert provider.cancels == [order.provider_order_id]
    order = await _webhook(session, order, "cancelled")  # провайдер подтверждает
    assert order.status == "cancelled"

    paid_then = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=FakeProvider()
    )
    paid_then = await _webhook(session, paid_then, "paid")
    with pytest.raises(DomainError) as exc_info:
        await payments.cancel_order(session, character, paid_then.id, provider)
    assert exc_info.value.code == "cannot_cancel"


@requires_db
async def test_refund_claws_back_eons(session, seeded):
    character = await _character(session, seeded)
    provider = FakeProvider()
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    order = await _webhook(session, order, "paid")
    assert await _eons(session, character) == 100

    order = await payments.refund_order(session, "owner", order.id, provider)
    assert provider.refunds == [order.provider_order_id]
    order = await _webhook(session, order, "refunded")  # провайдер подтверждает возврат
    assert order.status == "refunded"
    assert await _eons(session, character) == 0

    types = [
        t.type
        for t in (
            await session.scalars(
                sa.select(PaymentTransaction).where(PaymentTransaction.order_id == order.id)
            )
        ).all()
    ]
    assert "refund_initiated" in types
    assert "eons_clawed_back" in types
    assert "refund_confirmed" in types


@requires_db
async def test_refund_waits_when_eons_spent(session, seeded):
    """Эоны потрачены — возврат ждёт пополнения баланса (refund_pending_balance)."""
    character = await _character(session, seeded)
    provider = FakeProvider()
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    order = await _webhook(session, order, "paid")
    await economy.debit(session, character, economy.EON, 100, reason="shop_purchase")  # потратили

    order = await payments.refund_order(session, "owner", order.id, provider)
    order = await _webhook(session, order, "refunded")  # провайдер подтверждает
    assert order.status == "refund_pending_balance"

    await economy.credit(session, character, economy.EON, 50, reason="mint")
    status = await payments.retry_refund(session, order.id)
    assert status == "refund_pending_balance"  # 50 < 100 — всё ещё ждём

    await economy.credit(session, character, economy.EON, 60, reason="mint")
    status = await payments.retry_refund(session, order.id)
    assert status == "refunded"
    assert await _eons(session, character) == 10  # 110 - 100 изъято


@requires_db
async def test_journal_records_full_lifecycle(session, seeded):
    character = await _character(session, seeded)
    provider = FakeProvider()
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=provider
    )
    order = await _webhook(session, order, "paid")
    order = await payments.refund_order(session, "owner", order.id, provider)
    order = await _webhook(session, order, "refunded")

    txs = (
        await session.scalars(
            sa.select(PaymentTransaction)
            .where(PaymentTransaction.order_id == order.id)
            .order_by(PaymentTransaction.id)
        )
    ).all()
    seq = [t.type for t in txs]
    assert seq == [
        "order_created",
        "payment_confirmed",
        "eons_granted",
        "refund_initiated",
        "eons_clawed_back",
        "refund_confirmed",
    ]
    assert txs[0].amount_rub == 100 and txs[0].eons == 100


@requires_db
async def test_rub_rate_configurable(session, seeded):
    """Курс из config: eon.rub_rate=2 → 100 ₽ = 50 Эонов (по умолчанию 1:1)."""
    from app.models import ConfigParam

    param = await session.get(ConfigParam, "eon.rub_rate")
    assert param is not None
    param.value = 2
    await session.flush()

    character = await _character(session, seeded)
    order = await payments.create_order(
        session, character, offer_code="eon_pack_100", provider=FakeProvider()
    )
    assert order.eons == 50
