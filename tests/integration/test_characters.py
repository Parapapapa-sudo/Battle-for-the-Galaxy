"""Этап 1: аккаунты, создание персонажа, стартовая выдача, сиды."""
import uuid

import pytest
import sqlalchemy as sa

from app import characters
from app.models_game import (
    Account,
    Inventory,
    InventoryItem,
    ModerationName,
    Reputation,
    Ship,
)
from core.errors import DomainError
from tests.conftest import requires_db


async def _account(session) -> Account:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return account


@requires_db
async def test_first_contact_creates_account(session):
    account, created = await characters.get_or_create_account(
        session, platform="vk", platform_user_id="111"
    )
    assert created is True
    again, created_again = await characters.get_or_create_account(
        session, platform="vk", platform_user_id="111"
    )
    assert created_again is False
    assert again.id == account.id


@requires_db
async def test_create_character_full_kit(session, seeded):
    account = await _account(session)
    character = await characters.create_character(
        session, account_id=account.id, name="Иван Грозный", faction_code="azimuth"
    )

    assert character.status == "active"
    assert character.inventory_size == 30  # config_params default

    ship = await session.get(Ship, character.id)
    assert ship is not None
    assert ship.current_hull == ship.base_max_hull == 100
    assert ship.fuel == 50
    assert ship.status == "dock"

    inventories = {
        i.kind: i
        for i in (
            await session.scalars(
                sa.select(Inventory).where(Inventory.character_id == character.id)
            )
        ).all()
    }
    assert set(inventories) == {"ship", "warehouse", "waiting"}
    assert inventories["ship"].size_limit == 30
    assert inventories["warehouse"].size_limit == 20
    assert inventories["waiting"].size_limit is None  # безлимитный раздел (ТЗ)

    rep = await session.scalar(
        sa.select(Reputation).where(Reputation.character_id == character.id, Reputation.is_active)
    )
    assert rep is not None and rep.value == 0

    # стартовый набор: 10 металлолома + 2 ремкомплекта + 3 нанопластыря
    ship_inv = inventories["ship"]
    rows = (
        await session.execute(
            sa.select(InventoryItem.qty, InventoryItem.item_template_id).where(
                InventoryItem.inventory_id == ship_inv.id
            )
        )
    ).all()
    assert sum(r.qty for r in rows) == 15

    verdict = await session.scalar(
        sa.select(ModerationName.verdict).where(ModerationName.character_id == character.id)
    )
    assert verdict == "allowed"


@requires_db
async def test_duplicate_character_rejected(session, seeded):
    account = await _account(session)
    await characters.create_character(
        session, account_id=account.id, name="Первый", faction_code="helion"
    )
    with pytest.raises(DomainError) as exc_info:
        await characters.create_character(
            session, account_id=account.id, name="Второй", faction_code="helion"
        )
    assert exc_info.value.code == "character_exists"


@requires_db
@pytest.mark.parametrize(
    ("name", "code"),
    [("Аб", "name_too_short"), ("[Admin]Boss", "name_forbidden"), ("Нло!@#", "name_invalid_chars")],
)
async def test_name_rejections_logged(session, seeded, name, code):
    account = await _account(session)
    with pytest.raises(DomainError) as exc_info:
        await characters.create_character(
            session, account_id=account.id, name=name, faction_code="cinder"
        )
    assert exc_info.value.code == code
    verdict = await session.scalar(
        sa.select(ModerationName).where(ModerationName.verdict == "rejected")
    )
    assert verdict is not None and verdict.rule_hit is not None


@requires_db
async def test_unknown_faction(session, seeded):
    account = await _account(session)
    with pytest.raises(DomainError) as exc_info:
        await characters.create_character(
            session, account_id=account.id, name="Странник", faction_code="no_such"
        )
    assert exc_info.value.code == "unknown_faction"


@requires_db
async def test_seeds_idempotent(session):
    from app.seeds import ensure_seeds

    first = await ensure_seeds(session)
    assert first["factions"] == 4 and first["items"] == 4 and first["ships"] == 4
    assert first["events"] == 8 and first["event_tables"] == 1
    assert first["enemies"] == 3 and first["enemy_abilities"] == 1 and first["loot_tables"] == 3
    assert first["modules"] == 4 and first["recipes"] == 3 and first["shop_offers"] == 3
    assert first["quests"] == 4
    second = await ensure_seeds(session)
    assert second == {
        "factions": 0, "items": 0, "ships": 0, "events": 0, "event_tables": 0,
        "enemies": 0, "enemy_abilities": 0, "loot_tables": 0, "modules": 0, "recipes": 0,
        "shop_offers": 0, "quests": 0,
    }
