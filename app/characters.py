"""Use cases этапа 1: аккаунты, персонажи, инвентарь.

Всё исполняется внутри транзакции CommandBus — здесь только бизнес-логика.
"""
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.models_game import (
    Account,
    AccountIdentity,
    Character,
    Faction,
    Inventory,
    InventoryItem,
    ItemTemplate,
    ModerationName,
    Ship,
    ShipTemplate,
)
from core.errors import DomainError
from core.inventory import Stack, merge_into_stacks
from core.names import normalize_name, validate_name


async def get_or_create_account(
    session: AsyncSession, *, platform: str, platform_user_id: str
) -> tuple[Account, bool]:
    """Первое обращение с платформы → создаёт аккаунт и привязку (атомарно)."""
    identity = await session.scalar(
        sa.select(AccountIdentity).where(
            AccountIdentity.platform == platform,
            AccountIdentity.platform_user_id == platform_user_id,
        )
    )
    if identity is not None:
        account = await session.get(Account, identity.account_id)
        assert account is not None
        return account, False

    account = Account()
    session.add(account)
    await session.flush()
    session.add(
        AccountIdentity(
            account_id=account.id, platform=platform, platform_user_id=platform_user_id
        )
    )
    return account, True


async def ensure_account_active(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise DomainError("no_account", "Аккаунт не найден.")
    if account.status == "banned":
        raise DomainError("banned", "Аккаунт заблокирован. Обратитесь к администрации.")
    return account


async def get_active_character(
    session: AsyncSession, account_id: uuid.UUID
) -> Character | None:
    return await session.scalar(
        sa.select(Character).where(
            Character.account_id == account_id, Character.status == "active"
        )
    )


async def load_character(session: AsyncSession, character_id: uuid.UUID) -> Character:
    character = await session.get(Character, character_id)
    if character is None or character.status != "active":
        raise DomainError("no_character", "Персонаж не найден.")
    await ensure_account_active(session, character.account_id)
    return character


async def list_factions(session: AsyncSession) -> list[Faction]:
    return list(
        (await session.scalars(sa.select(Faction).order_by(Faction.code))).all()
    )


async def get_faction_by_code(session: AsyncSession, code: str) -> Faction | None:
    return await session.scalar(sa.select(Faction).where(Faction.code == code))


async def _name_config(session: AsyncSession) -> dict[str, Any]:
    return {
        "min_length": int(await stores.get_param(session, "names.min_length", 3)),
        "max_length": int(await stores.get_param(session, "names.max_length", 24)),
        "forbidden_patterns": list(
            await stores.get_param(session, "names.forbidden_patterns", [])
        ),
    }


async def create_character(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    name: str,
    faction_code: str,
) -> Character:
    """Создание персонажа по ТЗ: проверка имени, фракция, стартовый корабль и ресурсы."""
    await ensure_account_active(session, account_id)

    if await get_active_character(session, account_id) is not None:
        raise DomainError("character_exists", "У вас уже есть персонаж.")

    config = await _name_config(session)
    verdict = validate_name(name, **config)
    if verdict is not None:
        session.add(
            ModerationName(name=name, verdict="rejected", rule_hit=verdict)
        )
        messages = {
            "too_short": f"Имя слишком короткое (минимум {config['min_length']} символа).",
            "too_long": f"Имя слишком длинное (максимум {config['max_length']}).",
            "invalid_chars": "Имя содержит запрещённые символы.",
            "forbidden": "Имя запрещено правилами сообщества.",
        }
        raise DomainError(f"name_{verdict}", messages[verdict])

    faction = await get_faction_by_code(session, faction_code)
    if faction is None:
        raise DomainError("unknown_faction", f"Неизвестная фракция: {faction_code}")
    if faction.kind != "main":
        raise DomainError(
            "faction_not_joinable",
            "В эту фракцию нельзя вступить — она нейтральная.",
        )

    character = Character(
        account_id=account_id,
        name=normalize_name(name),
        name_normalized=normalize_name(name).lower(),
        faction_id=faction.id,
        inventory_size=int(await stores.get_param(session, "inventory.base_size", 30)),
        warehouse_size=int(await stores.get_param(session, "warehouse.base_size", 20)),
    )
    session.add(character)
    await session.flush()

    session.add(
        ModerationName(character_id=character.id, name=character.name, verdict="allowed")
    )
    # репутация: своя фракция — старт, остальные MAIN — штраф за вступление (ТЗ)
    from app import reputation as reputation_app

    await reputation_app.setup_faction_reputations(session, character, faction)
    session.add(
        Inventory(
            character_id=character.id,
            kind="ship",
            size_limit=character.inventory_size,
        )
    )
    session.add(
        Inventory(
            character_id=character.id,
            kind="warehouse",
            size_limit=character.warehouse_size,
        )
    )
    # «Ожидает получения» создаётся сразу — безлимитный раздел (ТЗ).
    session.add(Inventory(character_id=character.id, kind="waiting", size_limit=None))

    await _grant_starting_kit(session, character, faction)
    from app.quests import activate_first_quests

    await activate_first_quests(session, character)
    await session.flush()
    return character


async def _grant_starting_kit(
    session: AsyncSession, character: Character, faction: Faction
) -> None:
    resources = faction.start_resources or {}
    ship_code = resources.get("ship_template")
    if ship_code:
        template = await session.scalar(
            sa.select(ShipTemplate).where(ShipTemplate.code == ship_code)
        )
        if template is None:
            raise DomainError("content_error", f"Шаблон корабля не найден: {ship_code}")
        stats = template.base_stats or {}
        max_hull = int(stats.get("max_hull", 100))
        session.add(
            Ship(
                character_id=character.id,
                template_id=template.id,
                base_max_hull=max_hull,
                current_hull=max_hull,
                fuel=int(stats.get("fuel_capacity", 0)),
            )
        )
    for entry in resources.get("items", []):
        await grant_items(
            session,
            character,
            code=entry["code"],
            qty=int(entry["qty"]),
            allow_loss=True,  # стартовая выдача: при нехватке места излишек теряется (как прямой дроп)
        )
    for module_code in resources.get("modules", []):
        from app.modules import grant_module

        await grant_module(session, character, str(module_code))


async def get_inventory(
    session: AsyncSession, character_id: uuid.UUID, kind: str
) -> Inventory:
    inv = await session.scalar(
        sa.select(Inventory).where(
            Inventory.character_id == character_id, Inventory.kind == kind
        )
    )
    if inv is None:
        raise DomainError("no_inventory", "Хранилище не найдено.")
    return inv


async def grant_items(
    session: AsyncSession,
    character: Character,
    *,
    code: str,
    qty: int,
    kind: str = "ship",
    allow_loss: bool = False,
) -> int:
    """Выдача предметов с укладкой в стеки (core/inventory).

    Возвращает выданное количество. Не поместившийся остаток: DomainError,
    либо потеря при allow_loss=True (правило прямого дропа ТЗ). Результаты
    таймеров вместо этого уходят в waiting (этап 6) — разные ветки, тесты ТЗ.
    Уникальные предметы нельзя получить повторно (ТЗ) — остаток игнорируется
    без ошибки (идемпотентная выдача).
    """
    if qty <= 0:
        return 0
    template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == code))
    if template is None:
        raise DomainError("unknown_item", f"Неизвестный предмет: {code}")

    inventory = await get_inventory(session, character.id, kind)

    if template.is_unique:
        owned = await session.scalar(
            sa.select(sa.func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.inventory_id == inventory.id,
                InventoryItem.item_template_id == template.id,
            )
        )
        if owned:
            return 0  # уже есть — уникальный нельзя получить повторно (ТЗ)

    rows = (
        await session.scalars(
            sa.select(InventoryItem)
            .where(
                InventoryItem.inventory_id == inventory.id,
                InventoryItem.item_template_id == template.id,
            )
            .order_by(InventoryItem.acquired_at)
            .with_for_update()
        )
    ).all()
    stacks = [Stack(row.qty, template.stack_limit) for row in rows]
    # свободные слоты считаем по ВСЕМ ячейкам инвентаря, а не только этого предмета.
    # waiting («Ожидает получения») — безлимитный раздел (ТЗ).
    if inventory.size_limit is None:
        free_slots = qty  # хватит на любое число новых стеков
    else:
        total_stacks = int(
            await session.scalar(
                sa.select(sa.func.count()).select_from(InventoryItem).where(
                    InventoryItem.inventory_id == inventory.id
                )
            )
            or 0
        )
        free_slots = max(0, inventory.size_limit - total_stacks)

    new_stacks, leftover = merge_into_stacks(
        stacks, qty, limit=template.stack_limit, free_slots=free_slots
    )
    # первые len(rows) стеков обновляют существующие строки, остальные — новые ячейки
    for row, stack in zip(rows, new_stacks, strict=False):
        row.qty = stack.qty
    for stack in new_stacks[len(rows):]:
        session.add(
            InventoryItem(
                inventory_id=inventory.id,
                item_template_id=template.id,
                qty=stack.qty,
            )
        )

    if leftover > 0 and not allow_loss:
        raise DomainError("inventory_full", "Недостаточно места в инвентаре.")
    return qty - leftover


@dataclass
class InventoryRow:
    code: str
    name: str
    qty: int
    rarity: str
    category: str


async def remove_items(
    session: AsyncSession,
    character: Character,
    *,
    code: str,
    qty: int,
    kind: str = "ship",
) -> int:
    """Списание предметов (стеки с наименьшим количеством первыми). Возвращает списанное.

    При списании из инвентаря корабля освобождаются слоты — предметы из
    «Ожидает получения» автоматически перемещаются в инвентарь (ТЗ).
    """
    if qty <= 0:
        return 0
    template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == code))
    if template is None:
        raise DomainError("unknown_item", f"Неизвестный предмет: {code}")
    inventory = await get_inventory(session, character.id, kind)

    rows = (
        await session.scalars(
            sa.select(InventoryItem)
            .where(
                InventoryItem.inventory_id == inventory.id,
                InventoryItem.item_template_id == template.id,
                InventoryItem.qty > 0,
            )
            .order_by(InventoryItem.qty)
            .with_for_update()
        )
    ).all()
    remaining, removed = qty, 0
    for row in rows:
        take = min(row.qty, remaining)
        row.qty -= take
        removed += take
        remaining -= take
        if row.qty == 0:
            await session.delete(row)
        if remaining == 0:
            break
    await session.flush()
    if removed > 0 and kind == "ship":
        await flush_waiting(session, character)
    return removed


async def flush_waiting(session: AsyncSession, character: Character) -> int:
    """Перемещение предметов из «Ожидает получения» в инвентарь при наличии места (ТЗ).

    Возвращает число перемещённых предметов. Дроп с блокировкой не трогаем —
    это ветка результатов таймеров (крафт), которые сюда попадают уже после
    неудачной попытки выдачи в инвентарь.
    """
    waiting = await get_inventory(session, character.id, "waiting")
    rows = (
        await session.scalars(
            sa.select(InventoryItem)
            .where(InventoryItem.inventory_id == waiting.id)
            .order_by(InventoryItem.acquired_at, InventoryItem.id)
            .with_for_update()
        )
    ).all()
    moved = 0
    for row in rows:
        template = await session.get(ItemTemplate, row.item_template_id)
        assert template is not None
        original_qty = row.qty
        given = await grant_items(
            session, character, code=template.code, qty=original_qty, kind="ship", allow_loss=True
        )
        if given <= 0:
            break  # места больше нет
        moved += given
        row.qty = original_qty - given
        if row.qty <= 0:
            await session.delete(row)
        if given < original_qty:  # место кончилось на этом предмете
            break
    await session.flush()
    return moved


async def list_inventory(
    session: AsyncSession, character_id: uuid.UUID, kind: str = "ship"
) -> tuple[Inventory, list[InventoryRow]]:
    inventory = await get_inventory(session, character_id, kind)
    rows = (
        await session.execute(
            sa.select(ItemTemplate.code, ItemTemplate.name, InventoryItem.qty,
                      ItemTemplate.rarity, ItemTemplate.category)
            .join(InventoryItem, InventoryItem.item_template_id == ItemTemplate.id)
            .where(InventoryItem.inventory_id == inventory.id)
            .order_by(InventoryItem.acquired_at, InventoryItem.id)
        )
    ).all()
    items = [
        InventoryRow(code=r.code, name=r.name, qty=r.qty, rarity=r.rarity, category=r.category)
        for r in rows
    ]
    return inventory, items


async def get_ship(session: AsyncSession, character_id: uuid.UUID) -> tuple[Ship, ShipTemplate]:
    ship = await session.get(Ship, character_id)
    if ship is None:
        raise DomainError("no_ship", "Корабль не найден.")
    template = await session.get(ShipTemplate, ship.template_id)
    assert template is not None
    return ship, template
