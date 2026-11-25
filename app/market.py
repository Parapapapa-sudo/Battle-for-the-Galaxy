"""Аукцион, внутренний рынок Эонов, магазин и подписки.

ТЗ-критерии:
- аукцион: комиссия 1%, лимит лотов 5/10 (подписчик), уникальные не продаются,
  отмена без ставок → возврат предмета через 20 минут; установленный модуль требует снятия;
- ставки резервируют Кванты; перебитая ставка возвращается;
- рынок Эонов: ордерная книга, сведение worker'ом, защита отклонением курса от ориентира;
- подписка 7/30 дней за Эоны: +50% инвентаря, +слот крафта; бонусы снимаются по истечении;
- платежи: webhook идемпотентен по (provider, provider_payment_id) — ТЗ.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, economy, stores
from app.models import Timer
from app.models_game import (
    AuctionBid,
    AuctionLot,
    Character,
    EonMarketOrder,
    ItemTemplate,
    Purchase,
    ShopOffer,
    Subscription,
)
from core.errors import DomainError


async def _require_port(session: AsyncSession, character: Character) -> None:
    from app.expeditions import get_ship

    ship = await get_ship(session, character.id)
    if ship.status != "dock":
        raise DomainError("not_in_port", "Это доступно только в космопорту.")


async def _active_subscription(session: AsyncSession, character: Character) -> Subscription | None:
    return await session.scalar(
        sa.select(Subscription).where(
            Subscription.character_id == character.id,
            Subscription.status == "active",
            Subscription.expires_at > datetime.now(UTC),
        )
    )


# --- аукцион ----------------------------------------------------------------------


async def create_lot(
    session: AsyncSession,
    seller: Character,
    *,
    item_code: str,
    qty: int,
    min_price: int,
    buyout: int | None = None,
) -> AuctionLot:
    await _require_port(session, seller)
    template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == item_code))
    if template is None:
        raise DomainError("unknown_item", f"Неизвестный предмет: {item_code}")
    if template.is_unique:
        raise DomainError("unique_not_sellable", "Уникальные предметы нельзя продавать (ТЗ).")
    if not template.sellable:
        raise DomainError("not_sellable", f"«{template.name}» нельзя продать.")
    if qty <= 0 or min_price <= 0 or (buyout is not None and buyout < min_price):
        raise DomainError("bad_args", "Проверьте количество и цену.")

    sub = await _active_subscription(session, seller)
    limit = int(
        await stores.get_param(
            session,
            "auction.max_lots_subscriber" if sub else "auction.max_lots",
            10 if sub else 5,
        )
    )
    active = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(AuctionLot).where(
                AuctionLot.seller_id == seller.id, AuctionLot.status == "active"
            )
        )
        or 0
    )
    if active >= limit:
        raise DomainError("lot_limit", f"Лимит активных лотов: {limit}.")

    removed = await characters.remove_items(session, seller, code=item_code, qty=qty)
    if removed < qty:
        raise DomainError("no_item", f"Недостаточно «{item_code}» (нужно {qty}).")

    duration = int(await stores.get_param(session, "auction.duration_sec", 86_400))
    lot = AuctionLot(
        seller_id=seller.id,
        item_code=item_code,
        qty=qty,
        min_price_quant=min_price,
        buyout_quant=buyout,
        expires_at=datetime.now(UTC) + timedelta(seconds=duration),
    )
    session.add(lot)
    await session.flush()
    session.add(
        Timer(
            type="auction_expiry",
            character_id=seller.id,
            payload={"lot_id": str(lot.id)},
            fires_at=lot.expires_at,
        )
    )
    await session.flush()
    return lot


async def list_lots(session: AsyncSession) -> list[AuctionLot]:
    return list(
        (
            await session.scalars(
                sa.select(AuctionLot)
                .where(AuctionLot.status == "active")
                .order_by(AuctionLot.created_at.desc())
                .limit(20)
            )
        ).all()
    )


async def place_bid(
    session: AsyncSession, bidder: Character, lot_id: str, amount: int
) -> AuctionLot:
    await _require_port(session, bidder)
    lot = await _find_lot(session, lot_id)
    if lot.seller_id == bidder.id:
        raise DomainError("own_lot", "Нельзя ставить на свой лот.")
    current = lot.top_bid or lot.min_price_quant
    if amount <= current:
        raise DomainError("bid_too_low", f"Ставка должна быть выше {current}.")

    await economy.reserve(
        session, bidder, economy.QUANT, amount, reason="auction_bid", ref_id=str(lot.id)
    )
    if lot.top_bidder_id is not None and lot.top_bid:
        previous = await session.get(Character, lot.top_bidder_id)
        if previous is not None:
            await economy.unreserve(
                session, previous, economy.QUANT, lot.top_bid, reason="outbid", ref_id=str(lot.id)
            )
    lot.top_bid = amount
    lot.top_bidder_id = bidder.id
    session.add(AuctionBid(lot_id=lot.id, bidder_id=bidder.id, amount=amount))

    if lot.buyout_quant is not None and amount >= lot.buyout_quant:
        await _settle_lot(session, lot)
    await session.flush()
    return lot


async def cancel_lot(session: AsyncSession, seller: Character, lot_id: str) -> str:
    await _require_port(session, seller)
    lot = await _find_lot(session, lot_id)
    if lot.seller_id != seller.id:
        raise DomainError("not_your_lot", "Это не ваш лот.")
    if lot.top_bid is not None:
        raise DomainError("has_bids", "Нельзя отменить лот со ставками.")
    lot.status = "cancelled_pending_return"
    delay = int(await stores.get_param(session, "auction.cancel_return_sec", 1200))
    return_at = datetime.now(UTC) + timedelta(seconds=delay)
    await stores.cancel_timer(
        session, timer_type="auction_expiry", payload_id_key="lot_id", payload_id=str(lot.id)
    )
    session.add(
        Timer(
            type="auction_return",
            character_id=seller.id,
            payload={"lot_id": str(lot.id)},
            fires_at=return_at,
        )
    )
    await session.flush()
    return f"Лот отменён. Предмет вернётся через {delay // 60} минут (ТЗ)."


async def _settle_lot(session: AsyncSession, lot: AuctionLot) -> None:
    """Продажа: покупателю предмет (при переполнении — waiting), продавцу цена − комиссия."""
    commission_pct = int(await stores.get_param(session, "auction.commission_pct", 1))
    price = int(lot.top_bid or lot.min_price_quant)
    commission = price * commission_pct // 100

    seller = await session.get(Character, lot.seller_id)
    bidder = await session.get(Character, lot.top_bidder_id) if lot.top_bidder_id else None
    assert seller is not None
    if bidder is None:
        raise DomainError("no_bidder", "Нет ставок.")

    await economy.spend_reserved(
        session, bidder, economy.QUANT, price, reason="auction_buy", ref_id=str(lot.id)
    )
    await economy.credit(
        session, seller, economy.QUANT, price - commission, reason="auction_sale", ref_id=str(lot.id)
    )
    template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == lot.item_code))
    assert template is not None
    try:
        await characters.grant_items(
            session, bidder, code=lot.item_code, qty=lot.qty, allow_loss=False
        )
    except DomainError:
        await characters.grant_items(
            session, bidder, code=lot.item_code, qty=lot.qty, kind="waiting", allow_loss=True
        )
    lot.status = "sold"
    await stores.add_outbox(
        session, character_id=seller.id, text=f"Лот «{template.name}» продан за {price} Квантов."
    )


async def auction_expiry_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    lot = await session.get(AuctionLot, uuid.UUID(str(payload["lot_id"])))
    if lot is None or lot.status != "active":
        return
    if lot.top_bid is not None and lot.top_bidder_id is not None:
        await _settle_lot(session, lot)
    else:
        lot.status = "expired"
        await _return_lot_items(session, lot)
    await session.flush()


async def auction_return_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Возврат предмета после отмены лота (20 минут — ТЗ)."""
    lot = await session.get(AuctionLot, uuid.UUID(str(payload["lot_id"])))
    if lot is None or lot.status != "cancelled_pending_return":
        return
    await _return_lot_items(session, lot)
    await session.flush()


async def _return_lot_items(session: AsyncSession, lot: AuctionLot) -> None:
    seller = await session.get(Character, lot.seller_id)
    assert seller is not None
    await characters.grant_items(
        session, seller, code=lot.item_code, qty=lot.qty, allow_loss=True
    )
    lot.status = "expired" if lot.status == "cancelled_pending_return" else lot.status
    await stores.add_outbox(
        session, character_id=seller.id, text=f"Предмет «{lot.item_code}» ×{lot.qty} возвращён."
    )


async def _find_lot(session: AsyncSession, lot_id: str) -> AuctionLot:
    lots = (
        await session.scalars(
            sa.select(AuctionLot).where(AuctionLot.status == "active").order_by(AuctionLot.created_at.desc()).limit(50)
        )
    ).all()
    for lot in lots:
        if lot.id.hex.startswith(lot_id.lower()):
            return lot
    raise DomainError("no_lot", f"Нет активного лота «{lot_id}».")


# --- рынок Эонов -------------------------------------------------------------------


async def place_eon_order(
    session: AsyncSession, character: Character, *, side: str, amount: int, price: int
) -> EonMarketOrder:
    """Ордер buy/sell Эонов. Средства резервируются; сведение — worker."""
    if side not in ("buy", "sell"):
        raise DomainError("bad_side", "Сторона: buy | sell.")
    min_e = int(await stores.get_param(session, "market.order_min_eon", 1))
    max_e = int(await stores.get_param(session, "market.order_max_eon", 1000))
    if not min_e <= amount <= max_e:
        raise DomainError("bad_amount", f"Размер ордера вне лимита ({min_e}..{max_e} Эонов).")
    reference = int(await stores.get_param(session, "market.reference_price_quant", 1000))
    max_dev = int(await stores.get_param(session, "market.max_deviation_pct", 50))
    if abs(price - reference) * 100 > max_dev * reference:
        raise DomainError("price_deviation", "Цена слишком далеко от ориентира курса.")

    order = EonMarketOrder(
        character_id=character.id, side=side, amount_eon=amount, price_quant=price
    )
    session.add(order)
    await session.flush()
    if side == "sell":
        await economy.reserve(
            session, character, economy.EON, amount, reason="eon_sell", ref_id=str(order.id)
        )
    else:
        await economy.reserve(
            session, character, economy.QUANT, amount * price, reason="eon_buy", ref_id=str(order.id)
        )
    await session.flush()
    return order


async def settle_eon_market(session: AsyncSession) -> int:
    """Сведение ордеров: buy.price >= sell.price, сделка по цене sell (мейкер)."""
    buys = list(
        (
            await session.scalars(
                sa.select(EonMarketOrder)
                .where(EonMarketOrder.side == "buy", EonMarketOrder.status.in_(["open", "partial"]))
                .order_by(EonMarketOrder.price_quant.desc())
            )
        ).all()
    )
    sells = list(
        (
            await session.scalars(
                sa.select(EonMarketOrder)
                .where(EonMarketOrder.side == "sell", EonMarketOrder.status.in_(["open", "partial"]))
                .order_by(EonMarketOrder.price_quant.asc())
            )
        ).all()
    )
    deals = 0
    for buy in buys:
        for sell in sells:
            if buy.status == "matched" or sell.status == "matched":
                continue
            if buy.character_id == sell.character_id:
                continue
            if buy.price_quant < sell.price_quant:
                continue
            deal = min(buy.amount_eon - buy.filled_eon, sell.amount_eon - sell.filled_eon)
            if deal <= 0:
                continue
            price = sell.price_quant
            buyer = await session.get(Character, buy.character_id)
            seller = await session.get(Character, sell.character_id)
            assert buyer is not None and seller is not None

            await economy.spend_reserved(
                session, buyer, economy.QUANT, deal * price, reason="eon_buy", ref_id=str(buy.id)
            )
            await economy.spend_reserved(
                session, seller, economy.EON, deal, reason="eon_sell", ref_id=str(sell.id)
            )
            await economy.credit(
                session, seller, economy.QUANT, deal * price, reason="eon_sell", ref_id=str(sell.id)
            )
            await economy.credit(
                session, buyer, economy.EON, deal, reason="eon_buy", ref_id=str(buy.id)
            )
            buy.filled_eon += deal
            sell.filled_eon += deal
            buy.status = "matched" if buy.filled_eon >= buy.amount_eon else "partial"
            sell.status = "matched" if sell.filled_eon >= sell.amount_eon else "partial"
            deals += 1
    await session.flush()
    return deals


async def cancel_eon_order(session: AsyncSession, character: Character, order_id: str) -> None:
    orders = (
        await session.scalars(
            sa.select(EonMarketOrder)
            .where(EonMarketOrder.character_id == character.id, EonMarketOrder.status.in_(["open", "partial"]))
        )
    ).all()
    for order in orders:
        if order.id.hex.startswith(order_id.lower()):
            remaining = order.amount_eon - order.filled_eon
            if order.side == "sell":
                await economy.unreserve(
                    session, character, economy.EON, remaining, reason="eon_cancel", ref_id=str(order.id)
                )
            else:
                await economy.unreserve(
                    session, character, economy.QUANT, remaining * order.price_quant,
                    reason="eon_cancel", ref_id=str(order.id),
                )
            order.status = "cancelled"
            await session.flush()
            return
    raise DomainError("no_order", f"Нет открытого ордера «{order_id}».")


# --- магазин и подписки ------------------------------------------------------------


SUB_DURATION = {"sub_7": 7, "sub_30": 30}


async def list_offers(session: AsyncSession) -> list[ShopOffer]:
    return list(
        (
            await session.scalars(
                sa.select(ShopOffer).where(ShopOffer.status == "published")
            )
        ).all()
    )


async def buy_offer(session: AsyncSession, character: Character, offer_code: str) -> Purchase:
    """Покупка за Эоны: списываем Эоны (burn — в рубли не выводятся, ТЗ), выдаём payload."""
    offer = await session.scalar(
        sa.select(ShopOffer).where(ShopOffer.code == offer_code, ShopOffer.status == "published")
    )
    if offer is None:
        raise DomainError("unknown_offer", f"Нет предложения «{offer_code}».")

    await economy.debit(session, character, economy.EON, offer.price_eon, reason="shop_purchase")
    purchase = Purchase(
        character_id=character.id,
        offer_id=offer.id,
        provider="internal",
        provider_payment_id=f"internal-{uuid.uuid4()}",
        status="paid",
        paid_at=datetime.now(UTC),
    )
    session.add(purchase)
    await session.flush()

    if offer.kind == "subscription":
        kind = str(offer.payload.get("kind", "sub_7"))
        await grant_subscription(session, character, kind, purchase)
    return purchase


async def grant_subscription(
    session: AsyncSession, character: Character, kind: str, purchase: Purchase | None = None
) -> Subscription:
    if kind not in SUB_DURATION:
        raise DomainError("bad_subscription", "Подписка: sub_7 | sub_30.")
    now = datetime.now(UTC)
    days = SUB_DURATION[kind]

    active = await _active_subscription(session, character)
    if active is not None:
        active.status = "expired"  # новая заменяет старую
        base_inv = int(await stores.get_param(session, "inventory.base_size", 30))
        base_slots = 1
        character.inventory_size = base_inv
        character.craft_slots = base_slots

    sub = Subscription(
        character_id=character.id,
        kind=kind,
        started_at=now,
        expires_at=now + timedelta(days=days),
        source_purchase_id=purchase.id if purchase else None,
    )
    session.add(sub)

    bonus_pct = int(await stores.get_param(session, "subscription.inventory_bonus_pct", 50))
    bonus_slots = int(await stores.get_param(session, "subscription.craft_slots_bonus", 1))
    base_inv = int(await stores.get_param(session, "inventory.base_size", 30))
    character.inventory_size = base_inv * (100 + bonus_pct) // 100
    character.craft_slots = 1 + bonus_slots
    # обновляем лимит инвентаря корабля
    ship_inventory = await characters.get_inventory(session, character.id, "ship")
    ship_inventory.size_limit = character.inventory_size

    session.add(
        Timer(
            type="subscription_expiry",
            character_id=character.id,
            payload={"subscription_id": str(sub.id)},
            fires_at=sub.expires_at,
        )
    )
    await session.flush()
    return sub


async def subscription_expiry_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    sub = await session.get(Subscription, uuid.UUID(str(payload["subscription_id"])))
    if sub is None or sub.status != "active":
        return
    sub.status = "expired"
    character = await session.get(Character, sub.character_id)
    assert character is not None
    base_inv = int(await stores.get_param(session, "inventory.base_size", 30))
    character.inventory_size = base_inv
    character.craft_slots = 1
    ship_inventory = await characters.get_inventory(session, character.id, "ship")
    ship_inventory.size_limit = base_inv
    await stores.add_outbox(
        session, character_id=character.id, text="Подписка истекла. Бонусы отключены."
    )
    await session.flush()
