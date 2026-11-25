"""Кошельки (ledger-архитектура) и прямые передачи.

ТЗ-критерии:
- платежи/торговля атомарны и идемпотентны; Эоны не выводятся в рубли (mint только
  за покупки, burn только за покупки внутри игры);
- изменение баланса только через ledger (append-only) в той же транзакции;
- резервирование: до подтверждения/исполнения активы зарезервированы (ТЗ);
- прямая передача: только космопорт, лимиты/комиссии из config, пороги ценности —
  с подтверждением получателя; обычные исполняются автоматически; срок оффера из config.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, stores
from app.models import Timer
from app.models_game import Character, ItemTemplate, Transfer, Wallet, WalletLedger
from core.errors import DomainError

EON, QUANT = "eon", "quant"


async def _wallet(session: AsyncSession, character_id: uuid.UUID, currency: str) -> Wallet:
    wallet = await session.scalar(
        sa.select(Wallet).where(
            Wallet.character_id == character_id, Wallet.currency == currency
        )
    )
    if wallet is None:
        wallet = Wallet(character_id=character_id, currency=currency, balance=0, reserved=0)
        session.add(wallet)
        await session.flush()
    return wallet


async def get_wallet(session: AsyncSession, character_id: uuid.UUID, currency: str) -> Wallet:
    return await _wallet(session, character_id, currency)


async def _ledger(
    session: AsyncSession,
    wallet: Wallet,
    *,
    delta: int,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> None:
    wallet.balance += delta
    if wallet.balance < 0:  # защита: уход в минус невозможен
        raise DomainError("insufficient_funds", "Недостаточно средств.")
    session.add(
        WalletLedger(
            character_id=wallet.character_id,
            currency=wallet.currency,
            delta=delta,
            balance_after=wallet.balance,
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            idempotency_key=str(uuid.uuid4()),
        )
    )


async def credit(
    session: AsyncSession,
    character: Character,
    currency: str,
    amount: int,
    *,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> None:
    """Зачисление (mint/reward/refund)."""
    if amount <= 0:
        return
    wallet = await _wallet(session, character.id, currency)
    await _ledger(session, wallet, delta=amount, reason=reason, ref_type=ref_type, ref_id=ref_id)


async def debit(
    session: AsyncSession,
    character: Character,
    currency: str,
    amount: int,
    *,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> None:
    if amount <= 0:
        return
    wallet = await _wallet(session, character.id, currency)
    if wallet.balance < amount:
        raise DomainError(
            "insufficient_funds",
            f"Недостаточно {currency}: нужно {amount}, есть {wallet.balance}.",
        )
    await _ledger(session, wallet, delta=-amount, reason=reason, ref_type=ref_type, ref_id=ref_id)


async def reserve(
    session: AsyncSession,
    character: Character,
    currency: str,
    amount: int,
    *,
    reason: str,
    ref_id: str,
) -> None:
    """Резервирование средств (передачи/ставки): доступный баланс → reserved."""
    if amount <= 0:
        return
    wallet = await _wallet(session, character.id, currency)
    if wallet.balance < amount:
        raise DomainError(
            "insufficient_funds",
            f"Недостаточно {currency}: нужно {amount}, есть {wallet.balance}.",
        )
    wallet.balance -= amount
    wallet.reserved += amount
    session.add(
        WalletLedger(
            character_id=character.id,
            currency=currency,
            delta=-amount,
            balance_after=wallet.balance,
            reason=f"reserve:{reason}",
            ref_type="reserve",
            ref_id=ref_id,
            idempotency_key=str(uuid.uuid4()),
        )
    )


async def unreserve(
    session: AsyncSession,
    character: Character,
    currency: str,
    amount: int,
    *,
    reason: str,
    ref_id: str,
) -> None:
    """Возврат резерва на баланс (отмена/отказ/истечение)."""
    if amount <= 0:
        return
    wallet = await _wallet(session, character.id, currency)
    take = min(amount, wallet.reserved)
    wallet.reserved -= take
    wallet.balance += take
    session.add(
        WalletLedger(
            character_id=character.id,
            currency=currency,
            delta=take,
            balance_after=wallet.balance,
            reason=f"unreserve:{reason}",
            ref_type="reserve",
            ref_id=ref_id,
            idempotency_key=str(uuid.uuid4()),
        )
    )


async def spend_reserved(
    session: AsyncSession,
    character: Character,
    currency: str,
    amount: int,
    *,
    reason: str,
    ref_id: str,
) -> None:
    """Списание из резерва при исполнении сделки (reserved уменьшается, баланс не трогаем)."""
    wallet = await _wallet(session, character.id, currency)
    if wallet.reserved < amount:
        raise DomainError("reserve_broken", "Резерв недоступен.")
    wallet.reserved -= amount
    session.add(
        WalletLedger(
            character_id=character.id,
            currency=currency,
            delta=0,
            balance_after=wallet.balance,
            reason=f"spend_reserved:{reason}",
            ref_type="reserve",
            ref_id=ref_id,
            idempotency_key=str(uuid.uuid4()),
        )
    )


# --- прямые передачи -------------------------------------------------------------


async def _require_port(session: AsyncSession, character: Character) -> None:
    from app.expeditions import get_ship

    ship = await get_ship(session, character.id)
    if ship.status != "dock":
        raise DomainError("not_in_port", "Передача возможна только в космопорту.")


async def find_recipient(session: AsyncSession, name: str) -> Character:
    matches = (
        await session.scalars(
            sa.select(Character).where(
                Character.name_normalized == name.strip().lower(), Character.status == "active"
            )
        )
    ).all()
    if not matches:
        raise DomainError("no_recipient", f"Персонаж «{name}» не найден.")
    if len(matches) > 1:
        raise DomainError(
            "ambiguous_recipient", f"Имя «{name}» не уникально — уточните получателя."
        )
    return matches[0]


async def create_transfer(
    session: AsyncSession,
    sender: Character,
    recipient: Character,
    *,
    quant: int = 0,
    items: list[dict[str, Any]] | None = None,
) -> Transfer:
    """Создание передачи. Обычные исполняются автоматически; ценные — с подтверждением (ТЗ)."""
    await _require_port(session, sender)
    if recipient.id == sender.id:
        raise DomainError("self_transfer", "Нельзя передать самому себе.")
    commission_pct = int(await stores.get_param(session, "transfer.commission_pct", 0))
    max_quant = int(await stores.get_param(session, "transfer.max_quant", 100_000))

    if quant < 0 or quant > max_quant:
        raise DomainError("bad_amount", f"Сумма Квантов вне лимита (0..{max_quant}).")
    items = items or []
    rarity_valuable = ("rare", "epic", "unique")
    value_is_high = False
    for entry in items:
        template = await session.scalar(
            sa.select(ItemTemplate).where(ItemTemplate.code == str(entry["code"]))
        )
        if template is None:
            raise DomainError("unknown_item", f"Неизвестный предмет: {entry['code']}")
        if not template.tradable:
            raise DomainError("not_tradable", f"«{template.name}» нельзя передавать.")
        if template.rarity in rarity_valuable:
            value_is_high = True
    if quant >= int(await stores.get_param(session, "transfer.confirm_threshold_quant", 5_000)):
        value_is_high = True

    # списание/резерв отправителя
    quant_total = quant + quant * commission_pct // 100
    item_total: dict[str, int] = {}
    for entry in items:
        code, qty = str(entry["code"]), int(entry["qty"])
        removed = await characters.remove_items(session, sender, code=code, qty=qty)
        if removed < qty:
            raise DomainError("no_item", f"Недостаточно «{code}» (нужно {qty}).")
        item_total[code] = item_total.get(code, 0) + qty

    transfer = Transfer(
        from_id=sender.id,
        to_id=recipient.id,
        payload={"quant": quant, "items": [{"code": c, "qty": q} for c, q in item_total.items()]},
        status="pending_confirmation" if value_is_high else "active",
    )
    session.add(transfer)
    await session.flush()
    if quant_total > 0:
        await reserve(
            session, sender, QUANT, quant_total, reason="transfer", ref_id=str(transfer.id)
        )
    if value_is_high:
        ttl = int(await stores.get_param(session, "transfer.offer_ttl_sec", 86_400))
        transfer.expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        session.add(
            Timer(
                type="transfer_expiry",
                character_id=recipient.id,
                payload={"transfer_id": str(transfer.id)},
                fires_at=transfer.expires_at,
            )
        )
        await stores.add_outbox(
            session,
            character_id=recipient.id,
            text=(
                f"Входящая передача от «{sender.name}»: {quant} Квантов, "
                f"{len(item_total)} позиций. Подтвердите: confirm {transfer.id.hex[:8]} "
                f"(или decline {transfer.id.hex[:8]}). Срок: {ttl // 3600}ч."
            ),
        )
    else:
        await _execute_transfer(session, transfer)
    await session.flush()
    return transfer


async def _execute_transfer(session: AsyncSession, transfer: Transfer) -> None:
    """Исполнение: кванты (с комиссией) отправителя получателю, предметы — в инвентарь."""
    sender = await session.get(Character, transfer.from_id)
    recipient = await session.get(Character, transfer.to_id)
    assert sender is not None and recipient is not None
    quant = int(transfer.payload.get("quant", 0))
    if quant > 0:
        commission_pct = int(await stores.get_param(session, "transfer.commission_pct", 0))
        commission = quant * commission_pct // 100
        await spend_reserved(
            session, sender, QUANT, quant + commission, reason="transfer", ref_id=str(transfer.id)
        )
        await credit(
            session, recipient, QUANT, quant, reason="transfer_in", ref_id=str(transfer.id)
        )
    for entry in transfer.payload.get("items", []):
        template = await session.scalar(
            sa.select(ItemTemplate).where(ItemTemplate.code == str(entry["code"]))
        )
        assert template is not None
        given = await characters.grant_items(
            session, recipient, code=template.code, qty=int(entry["qty"]), allow_loss=True
        )
        if given < int(entry["qty"]):  # инвентарь получателя полон — возврат отправителю (допущение D)
            await characters.grant_items(
                session, sender, code=template.code, qty=int(entry["qty"]) - given, allow_loss=True
            )
            await stores.add_outbox(
                session,
                character_id=sender.id,
                text=f"Передача «{template.name}» отклонена: инвентарь получателя полон.",
            )
    transfer.status = "completed"
    transfer.completed_at = datetime.now(UTC)
    await stores.add_outbox(
        session,
        character_id=sender.id,
        text=f"Передача «{recipient.name}» исполнена.",
    )


async def confirm_transfer(session: AsyncSession, character: Character, short_id: str) -> None:
    transfer = await _find_pending(session, character, short_id)
    transfer.status = "active"
    transfer.confirmed_at = datetime.now(UTC)
    await stores.cancel_timer(
        session, timer_type="transfer_expiry", payload_id_key="transfer_id", payload_id=str(transfer.id)
    )
    await _execute_transfer(session, transfer)
    await session.flush()


async def reject_transfer(session: AsyncSession, character: Character, short_id: str) -> None:
    transfer = await _find_pending(session, character, short_id)
    transfer.status = "rejected"
    await _refund_transfer(session, transfer)
    await session.flush()


async def transfer_expiry_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    transfer = await session.get(Transfer, uuid.UUID(str(payload["transfer_id"])))
    if transfer is None or transfer.status != "pending_confirmation":
        return
    transfer.status = "expired"
    await _refund_transfer(session, transfer)
    await session.flush()


async def _refund_transfer(session: AsyncSession, transfer: Transfer) -> None:
    sender = await session.get(Character, transfer.from_id)
    assert sender is not None
    quant = int(transfer.payload.get("quant", 0))
    if quant > 0:
        await unreserve(
            session, sender, QUANT, quant, reason="transfer_refund", ref_id=str(transfer.id)
        )
    for entry in transfer.payload.get("items", []):
        await characters.grant_items(
            session, sender, code=str(entry["code"]), qty=int(entry["qty"]), allow_loss=True
        )
    await stores.cancel_timer(
        session, timer_type="transfer_expiry", payload_id_key="transfer_id", payload_id=str(transfer.id)
    )
    await stores.add_outbox(
        session, character_id=sender.id, text="Передача не принята — активы возвращены."
    )


async def _find_pending(
    session: AsyncSession, character: Character, short_id: str
) -> Transfer:
    transfers = (
        await session.scalars(
            sa.select(Transfer).where(
                Transfer.to_id == character.id, Transfer.status == "pending_confirmation"
            )
        )
    ).all()
    for transfer in transfers:
        if transfer.id.hex.startswith(short_id.lower()):
            return transfer
    raise DomainError("no_transfer", f"Нет ожидающей передачи «{short_id}».")


async def pending_transfers(session: AsyncSession, character: Character) -> list[Transfer]:
    return list(
        (
            await session.scalars(
                sa.select(Transfer)
                .where(Transfer.to_id == character.id, Transfer.status == "pending_confirmation")
                .order_by(Transfer.created_at)
            )
        ).all()
    )
