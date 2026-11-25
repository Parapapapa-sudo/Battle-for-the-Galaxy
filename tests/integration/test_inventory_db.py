"""Инвентарь: укладка в стеки на уровне БД, уникальные предметы, переполнение."""
import uuid

import pytest
import sqlalchemy as sa

from app import characters
from app.models_game import Character, InventoryItem, ItemTemplate
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )


@requires_db
async def test_grant_stacks_merge(session, seeded):
    character = await _character(session, seeded)
    given = await characters.grant_items(session, character, code="scrap_metal", qty=10)
    assert given == 10
    given = await characters.grant_items(session, character, code="scrap_metal", qty=60)
    assert given == 60

    inventory = await characters.get_inventory(session, character.id, "ship")
    rows = (
        await session.execute(
            sa.select(InventoryItem.qty, ItemTemplate.code)
            .join(ItemTemplate, ItemTemplate.id == InventoryItem.item_template_id)
            .where(
                InventoryItem.inventory_id == inventory.id,
                ItemTemplate.code == "scrap_metal",
            )
        )
    ).all()
    # 80 лома (10 старт + 10 + 60), лимит стека 50 → стеки 50 + 30
    assert sorted(r.qty for r in rows) == [30, 50]


@requires_db
async def test_unique_item_not_granted_twice(session, seeded):
    character = await _character(session, seeded)
    # уникальный предмет-заглушка
    session.add(
        ItemTemplate(
            code=f"unique_rel_{uuid.uuid4().hex[:6]}",
            name="Уникальный артефакт",
            category="special",
            rarity="unique",
            stack_limit=1,
            is_unique=True,
            sellable=False,
            tradable=False,
        )
    )
    await session.flush()

    template = (
        await session.scalars(sa.select(ItemTemplate).where(ItemTemplate.is_unique))
    ).first()
    assert template is not None

    first = await characters.grant_items(session, character, code=template.code, qty=1)
    second = await characters.grant_items(session, character, code=template.code, qty=1)
    assert first == 1
    assert second == 0  # уникальный уже есть — повторно нельзя (ТЗ)


@requires_db
async def test_inventory_full_raises_unless_allow_loss(session, seeded):
    # allow_loss=True — прямой дроп: не поместившееся теряется (ТЗ), ошибки нет.
    # Отдельный персонаж — чтобы грязное состояние после отклонённой выдачи
    # не влияло на вторую проверку (в проде rollback транзакции всё отменяет).
    character = await _character(session, seeded)
    inventory = await characters.get_inventory(session, character.id, "ship")
    occupied = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(InventoryItem).where(
                InventoryItem.inventory_id == inventory.id
            )
        )
    )
    free = character.inventory_size - occupied  # 30 - 2 стартовых стека = 28

    given = await characters.grant_items(
        session, character, code="scrap_metal", qty=(free + 2) * 50, allow_loss=True
    )
    # ёмкость: дочерновка текущего стека лома (40) + free новых ячеек по 50
    assert given == 40 + free * 50

    character2 = await _character(session, seeded)
    with pytest.raises(DomainError) as exc_info:
        await characters.grant_items(
            session, character2, code="scrap_metal", qty=(free + 1) * 50 + 10,
            allow_loss=False,
        )
    assert exc_info.value.code == "inventory_full"


@requires_db
async def test_list_inventory_joins_templates(session, seeded):
    character = await _character(session, seeded)
    inventory, rows = await characters.list_inventory(session, character.id)
    assert inventory.kind == "ship"
    codes = {r.code for r in rows}
    assert {"scrap_metal", "repair_kit"} <= codes
    assert all(r.qty > 0 for r in rows)
