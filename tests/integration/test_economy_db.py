"""Этап 7: экономика — целостность денег, резервы, передачи, аукцион, рынок, подписки."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app import characters, economy, market
from app.models import Timer
from app.models_game import (
    Character,
    ItemTemplate,
    Subscription,
    WalletLedger,
)
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, name: str | None = None) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session,
        account_id=account.id,
        name=name or f"Тестер{uuid.uuid4().hex[:6]}",
        faction_code="azimuth",
    )
    await session.flush()
    return character


async def _balance(session, character, currency) -> tuple[int, int]:
    wallet = await economy.get_wallet(session, character.id, currency)
    return wallet.balance, wallet.reserved


@requires_db
async def test_wallet_ledger_integrity(session, seeded):
    character = await _character(session, seeded)
    await economy.credit(session, character, economy.QUANT, 1000, reason="test_mint")

    balance, reserved = await _balance(session, character, economy.QUANT)
    assert (balance, reserved) == (1000, 0)

    await economy.debit(session, character, economy.QUANT, 300, reason="test_pay")
    balance, _ = await _balance(session, character, economy.QUANT)
    assert balance == 700

    rows = (
        await session.scalars(
            sa.select(WalletLedger).where(WalletLedger.character_id == character.id)
        )
    ).all()
    assert [r.delta for r in rows] == [1000, -300]
    assert rows[-1].balance_after == 700  # контрольная сумма после каждой операции

    with pytest.raises(DomainError) as exc_info:
        await economy.debit(session, character, economy.QUANT, 99999, reason="overdraft")
    assert exc_info.value.code == "insufficient_funds"
    balance, _ = await _balance(session, character, economy.QUANT)
    assert balance == 700  # баланс не ушёл в минус


@requires_db
async def test_reserve_unreserve_cycle(session, seeded):
    character = await _character(session, seeded)
    await economy.credit(session, character, economy.QUANT, 500, reason="mint")
    await economy.reserve(session, character, economy.QUANT, 200, reason="deal", ref_id="d1")
    balance, reserved = await _balance(session, character, economy.QUANT)
    assert (balance, reserved) == (300, 200)

    await economy.unreserve(session, character, economy.QUANT, 200, reason="refund", ref_id="d1")
    balance, reserved = await _balance(session, character, economy.QUANT)
    assert (balance, reserved) == (500, 0)

    # spend_reserved: баланс не трогает, резерв уменьшает
    await economy.reserve(session, character, economy.QUANT, 100, reason="deal", ref_id="d2")
    await economy.spend_reserved(session, character, economy.QUANT, 100, reason="deal", ref_id="d2")
    balance, reserved = await _balance(session, character, economy.QUANT)
    assert (balance, reserved) == (400, 0)


@requires_db
async def test_transfer_auto_executes_below_threshold(session, seeded):
    sender = await _character(session, seeded)
    receiver = await _character(session, seeded, name=f"Получатель{uuid.uuid4().hex[:4]}")
    await economy.credit(session, sender, economy.QUANT, 1000, reason="mint")

    transfer = await economy.create_transfer(session, sender, receiver, quant=100)
    assert transfer.status == "completed"  # обычные исполняются автоматически (ТЗ)

    s_balance, _ = await _balance(session, sender, economy.QUANT)
    r_balance, _ = await _balance(session, receiver, economy.QUANT)
    assert s_balance == 900 and r_balance == 100


@requires_db
async def test_transfer_with_confirmation_flow(session, seeded):
    """Ценные передачи: резерв → подтверждение получателя → исполнение (ТЗ)."""
    sender = await _character(session, seeded)
    receiver = await _character(session, seeded, name=f"Босс{uuid.uuid4().hex[:4]}")
    await economy.credit(session, sender, economy.QUANT, 10_000, reason="mint")

    transfer = await economy.create_transfer(session, sender, receiver, quant=9_000)
    assert transfer.status == "pending_confirmation"
    s_balance, s_reserved = await _balance(session, sender, economy.QUANT)
    assert (s_balance, s_reserved) == (1_000, 9_000)  # активы зарезервированы (ТЗ)

    short = transfer.id.hex[:8]
    await economy.confirm_transfer(session, receiver, short)
    s_balance, s_reserved = await _balance(session, sender, economy.QUANT)
    r_balance, _ = await _balance(session, receiver, economy.QUANT)
    assert (s_balance, s_reserved) == (1_000, 0)
    assert r_balance == 9_000

    timer = await session.scalar(
        sa.select(Timer).where(Timer.type == "transfer_expiry", Timer.payload["transfer_id"].astext == str(transfer.id))
    )
    assert timer is not None and timer.status == "cancelled"


@requires_db
async def test_transfer_decline_and_expiry_refund(session, seeded, bus):

    sender = await _character(session, seeded)
    receiver = await _character(session, seeded, name=f"Отказник{uuid.uuid4().hex[:4]}")
    await economy.credit(session, sender, economy.QUANT, 10_000, reason="mint")

    transfer = await economy.create_transfer(session, sender, receiver, quant=6_000)
    await economy.reject_transfer(session, receiver, transfer.id.hex[:8])
    s_balance, s_reserved = await _balance(session, sender, economy.QUANT)
    assert (s_balance, s_reserved) == (10_000, 0)  # возврат резерва

    # истечение срока
    transfer2 = await economy.create_transfer(session, sender, receiver, quant=5_000)
    await economy.transfer_expiry_handler(session, {"transfer_id": str(transfer2.id)})
    s_balance, s_reserved = await _balance(session, sender, economy.QUANT)
    assert (s_balance, s_reserved) == (10_000, 0)

    # идемпотентность: повторная обработка того же таймера
    await economy.transfer_expiry_handler(session, {"transfer_id": str(transfer2.id)})


@requires_db
async def test_auction_full_cycle(session, seeded, bus):
    """Лот → ставки (резерв) → перебив (возврат) → истечение → расчёт с комиссией."""
    seller = await _character(session, seeded)
    buyer = await _character(session, seeded, name=f"Покупатель{uuid.uuid4().hex[:4]}")
    outbidder = await _character(session, seeded, name=f"Перебивщик{uuid.uuid4().hex[:4]}")
    await economy.credit(session, buyer, economy.QUANT, 1_000, reason="mint")
    await economy.credit(session, outbidder, economy.QUANT, 2_000, reason="mint")

    lot = await market.create_lot(
        session, seller, item_code="scrap_metal", qty=10, min_price=100, buyout=None
    )
    _, items = await characters.list_inventory(session, seller.id)
    assert sum(r.qty for r in items if r.code == "scrap_metal") == 0  # предмет снят с продажи

    await market.place_bid(session, buyer, lot.id.hex[:8], 150)
    _, reserved = await _balance(session, buyer, economy.QUANT)
    assert reserved == 150

    await market.place_bid(session, outbidder, lot.id.hex[:8], 200)
    _, reserved_b = await _balance(session, buyer, economy.QUANT)
    _, reserved_o = await _balance(session, outbidder, economy.QUANT)
    assert reserved_b == 0  # перебитая ставка возвращена
    assert reserved_o == 200

    # истечение лота: продажа перебивщику
    lot.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await market.auction_expiry_handler(session, {"lot_id": str(lot.id)})
    await session.refresh(lot)
    assert lot.status == "sold"

    s_balance, _ = await _balance(session, seller, economy.QUANT)
    assert s_balance == 200 - 2  # комиссия 1% (ТЗ)
    _, items = await characters.list_inventory(session, outbidder.id)
    assert sum(r.qty for r in items if r.code == "scrap_metal") == 20  # 10 стартовых + 10 купленных


@requires_db
async def test_auction_rules(session, seeded):
    seller = await _character(session, seeded)
    unique = ItemTemplate(
        code=f"uniq_{uuid.uuid4().hex[:6]}",
        name="Уникум",
        category="special",
        rarity="unique",
        stack_limit=1,
        is_unique=True,
        sellable=False,
    )
    session.add(unique)
    await session.flush()
    await characters.grant_items(session, seller, code=unique.code, qty=1, allow_loss=True)

    with pytest.raises(DomainError) as exc_info:
        await market.create_lot(session, seller, item_code=unique.code, qty=1, min_price=10)
    assert exc_info.value.code == "unique_not_sellable"

    # отмена без ставок → возврат через таймер (ТЗ, 20 минут)
    lot = await market.create_lot(session, seller, item_code="scrap_metal", qty=5, min_price=10)
    text = await market.cancel_lot(session, seller, lot.id.hex[:8])
    assert "20 мин" in text or "вернётся" in text
    await session.refresh(lot)
    assert lot.status == "cancelled_pending_return"

    timer = await session.scalar(
        sa.select(Timer).where(Timer.type == "auction_return", Timer.status == "pending")
    )
    assert timer is not None
    await market.auction_return_handler(session, {"lot_id": str(lot.id)})
    await session.refresh(lot)
    _, items = await characters.list_inventory(session, seller.id)
    assert sum(r.qty for r in items if r.code == "scrap_metal") == 10  # 5 осталось от стартовых + 5 возвращено


@requires_db
async def test_eon_market_settlement(session, seeded):
    seller = await _character(session, seeded)
    buyer = await _character(session, seeded, name=f"Трейдер{uuid.uuid4().hex[:4]}")
    await economy.credit(session, seller, economy.EON, 100, reason="mint")
    await economy.credit(session, buyer, economy.QUANT, 50_000, reason="mint")

    sell = await market.place_eon_order(session, seller, side="sell", amount=30, price=1_000)
    buy = await market.place_eon_order(session, buyer, side="buy", amount=20, price=1_100)

    # цена сделки — по sell (мейкер), частичное исполнение sell
    deals = await market.settle_eon_market(session)
    assert deals == 1
    await session.refresh(sell)
    await session.refresh(buy)
    assert buy.status == "matched" and sell.status == "partial"
    assert sell.filled_eon == 20

    b_eon, _ = await _balance(session, buyer, economy.EON)
    s_quant, _ = await _balance(session, seller, economy.QUANT)
    assert b_eon == 20
    assert s_quant == 20 * 1_000

    # отклонение курса от ориентира — защита от манипуляций
    with pytest.raises(DomainError) as exc_info:
        await market.place_eon_order(session, buyer, side="buy", amount=1, price=10_000)
    assert exc_info.value.code == "price_deviation"

    # отмена остатка ордера возвращает резерв
    await market.cancel_eon_order(session, seller, sell.id.hex[:8])
    s_eon, s_eon_r = await _balance(session, seller, economy.EON)
    assert (s_eon, s_eon_r) == (80, 0)


@requires_db
async def test_subscription_buy_and_expiry(session, seeded):
    character = await _character(session, seeded)
    await economy.credit(session, character, economy.EON, 500, reason="mint")

    await market.buy_offer(session, character, "sub_7")
    sub = await session.scalar(
        sa.select(Subscription).where(Subscription.character_id == character.id, Subscription.status == "active")
    )
    assert sub is not None and sub.kind == "sub_7"
    eon_balance, _ = await _balance(session, character, economy.EON)
    assert eon_balance == 500 - 99

    # бонусы: +50% инвентаря, +слот крафта (ТЗ)
    assert character.inventory_size == 45
    assert character.craft_slots == 2

    # истечение — бонусы снимаются
    await market.subscription_expiry_handler(session, {"subscription_id": str(sub.id)})
    assert character.inventory_size == 30
    assert character.craft_slots == 1
    # идемпотентно
    await market.subscription_expiry_handler(session, {"subscription_id": str(sub.id)})


@requires_db
async def test_payment_webhook_idempotent(session, seeded):
    character = await _character(session, seeded)
    p1 = await market.process_payment_webhook(
        session,
        provider="mock",
        provider_payment_id="pay-123",
        character_id=character.id,
        offer_code="eon_pack_100",
        status="paid",
    )
    p2 = await market.process_payment_webhook(
        session,
        provider="mock",
        provider_payment_id="pay-123",
        character_id=character.id,
        offer_code="eon_pack_100",
        status="paid",
    )
    assert p1.id == p2.id  # повтор webhook'а не дублирует (ТЗ)
    eon_balance, _ = await _balance(session, character, economy.EON)
    assert eon_balance == 100  # зачислено ровно один раз
