"""Этап 9: админ-панель — RBAC, аудит, модерация, конфигурация, контент, аналитика."""
import uuid

import pytest
import sqlalchemy as sa

from app import admin, characters
from app.models import AuditLog
from app.models_game import AdminUser, Character, CharacterEffect
from core.errors import DomainError
from tests.conftest import requires_db

OWNER_KEY = "dev-only-owner-key"


async def _admins(session) -> tuple[AdminUser, AdminUser, AdminUser]:
    """owner (admin), moderator, content_manager."""
    mod = AdminUser(login="mod1", api_key_hash=admin.hash_key("mod-key"), role="moderator")
    cm = AdminUser(
        login="cm1", api_key_hash=admin.hash_key("cm-key"), role="content_manager"
    )
    session.add_all([mod, cm])
    await session.flush()
    owner = await session.scalar(sa.select(AdminUser).where(AdminUser.login == "owner"))
    assert owner is not None
    return owner, mod, cm


async def _character(session, seeded) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code="azimuth"
    )


@requires_db
async def test_authenticate_and_role_matrix(session, seeded):
    owner, mod, cm = await _admins(session)
    assert (await admin.authenticate(session, OWNER_KEY)).login == "owner"
    with pytest.raises(DomainError):
        await admin.authenticate(session, "wrong-key")

    character = await _character(session, seeded)
    # модератор: бан и эффекты — можно; конфиги — нельзя
    await admin.ban_account(session, mod, character.account_id, reason="тест")
    await admin.unban_account(session, mod, character.account_id)
    await admin.apply_effect(session, mod, character.id, "xp_boost", params={"power": 2})
    with pytest.raises(DomainError) as exc_info:
        await admin.set_config(session, mod, "inventory.base_size", 40)
    assert exc_info.value.code == "forbidden"

    # контент-менеджер: публикация в своей зоне — можно; бан — нельзя
    await admin.set_content_status(session, cm, "event_templates", "quiet_drift", "draft")
    with pytest.raises(DomainError) as exc_info:
        await admin.ban_account(session, cm, character.account_id, reason="x")
    assert exc_info.value.code == "forbidden"
    with pytest.raises(DomainError) as exc_info:
        await admin.set_config(session, cm, "inventory.base_size", 40)
    assert exc_info.value.code == "forbidden"


@requires_db
async def test_item_editor_create_update_validate(session, seeded):
    """Вкладка «Предметы»: создание, изменение, валидация, аудит."""
    owner, mod, cm = await _admins(session)

    # справочник: предметы + типы + редкости
    catalog = await admin.list_items(session, owner)
    assert catalog["items"] and "resource" in catalog["categories"]
    assert "common" in catalog["rarities"]
    with pytest.raises(DomainError) as exc_info:
        await admin.list_items(session, mod)  # предметы — зона админа
    assert exc_info.value.code == "forbidden"

    # создание
    created = await admin.create_item(session, owner, {
        "code": "test_crystal", "name": "Тестовый кристалл", "category": "resource",
        "rarity": "rare", "stack_limit": 5, "drop_weight": 50,
        "drop_qty_min": 1, "drop_qty_max": 3, "is_unique": False,
    })
    assert created["code"] == "test_crystal" and created["drop_weight"] == 50

    # дубликат кода — ошибка
    with pytest.raises(DomainError) as exc_info:
        await admin.create_item(session, owner, {
            "code": "test_crystal", "name": "Дубль", "category": "resource",
        })
    assert "уже существует" in exc_info.value.message

    # изменение параметров
    updated = await admin.update_item(session, owner, "test_crystal", {
        "name": "Кристалл глубин", "category": "special", "rarity": "epic",
        "stack_limit": 2, "drop_weight": 100, "drop_qty_min": 2, "drop_qty_max": 2,
        "is_unique": True, "sellable": False, "tradable": False,
        "losable_on_defeat": False, "description": "Редкий кристалл",
    })
    assert updated["name"] == "Кристалл глубин" and updated["is_unique"] is True
    assert updated["drop_qty_min"] == updated["drop_qty_max"] == 2

    # валидация: пустое название, кривой диапазон, плохая редкость, дроп > 100%
    for bad in (
        {"name": "", "category": "resource"},
        {"name": "X", "category": "resource", "drop_qty_min": 5, "drop_qty_max": 2},
        {"name": "X", "category": "resource", "rarity": "legendary"},
        {"name": "X", "category": "resource", "drop_weight": 150},
    ):
        with pytest.raises(DomainError) as exc_info:
            await admin.update_item(session, owner, "test_crystal", bad)
        assert exc_info.value.code == "bad_item"

    # аудит: create + update записаны
    actions = (
        await session.scalars(
            sa.select(AuditLog.action).where(AuditLog.target_id == "test_crystal")
        )
    ).all()
    assert "create_item" in actions and "update_item" in actions


@requires_db
async def test_expedition_bonus_drop(session, seeded):
    """drop_weight=100%: предмет гарантированно падает после события экспедиции."""
    import random

    from app import expeditions
    from app.models_game import ItemTemplate, Ship

    character = await _character(session, seeded)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 50
    template = await session.scalar(
        sa.select(ItemTemplate).where(ItemTemplate.code == "scrap_metal")
    )
    assert template is not None
    template.drop_weight = 100
    template.drop_qty_min = template.drop_qty_max = 2
    await session.flush()

    expedition = await expeditions.launch_expedition(session, character)
    await expeditions.proceed(session, character, rng=random.Random(1))
    await session.refresh(expedition)
    from datetime import UTC, datetime, timedelta

    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.arrive_event(
        session, {"expedition_id": str(expedition.id)}, rng=random.Random(1)
    )
    # если событие интерактивное — закрываем БЕЗОПАСНЫМ выбором
    # (боевой исход не даёт бонусный дроп: у врагов свои таблицы добычи)
    offered = await expeditions._current_offered_event(session, expedition)
    if offered is not None:
        choices = offered.snapshot["script"]["choices"]
        safe = next((c for c in choices if "battle" not in (c.get("outcome") or {})), choices[0])
        await expeditions.resolve_choice(session, character, str(safe["key"]), rng=random.Random(1))

    _, rows = await characters.list_inventory(session, character.id)
    scrap = next((r for r in rows if r.code == "scrap_metal"), None)
    assert scrap is not None and scrap.qty >= 12  # 10 стартовых + 2 бонусных

    template.drop_weight = 0  # откат, чтобы не влиять на другие тесты
    await session.flush()


@requires_db
async def test_audit_log_written(session, seeded):
    owner, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    await admin.ban_account(session, mod, character.account_id, reason="нарушение")
    await admin.set_config(session, owner, "inventory.base_size", 35)

    rows = (
        await session.scalars(
            sa.select(AuditLog).where(AuditLog.actor_id.in_(["mod1", "owner"]))
        )
    ).all()
    actions = {r.action for r in rows}
    assert "ban_account" in actions
    assert "set_config" in actions
    ban = next(r for r in rows if r.action == "ban_account")
    assert ban.actor_role == "moderator"
    assert ban.before is not None and ban.after is not None  # diff (ТЗ: аудит)


@requires_db
async def test_ban_blocks_game_commands(session, seeded):
    _, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    await admin.ban_account(session, mod, character.account_id, reason="тест")
    with pytest.raises(DomainError) as exc_info:
        await characters.ensure_account_active(session, character.account_id)
    assert exc_info.value.code == "banned"


@requires_db
async def test_config_hot_reload_applies(session, seeded):
    """ТЗ: баланс правится без кода и перезапуска — игра читает новое значение."""
    owner, _, _ = await _admins(session)
    await admin.set_config(session, owner, "inventory.base_size", 35)
    character = await _character(session, seeded)  # создаётся ПОСЛЕ правки
    assert character.inventory_size == 35


@requires_db
async def test_moderator_cannot_create_effects(session, seeded):
    """ТЗ: модератор применяет только существующие эффекты."""
    _, mod, _ = await _admins(session)
    character = await _character(session, seeded)
    with pytest.raises(DomainError) as exc_info:
        await admin.apply_effect(session, mod, character.id, "nonexistent_effect")
    assert exc_info.value.code == "no_effect"
    await admin.apply_effect(session, mod, character.id, "xp_boost")
    effects = (
        await session.scalars(
            sa.select(CharacterEffect).where(CharacterEffect.character_id == character.id)
        )
    ).all()
    assert len(effects) == 1


@requires_db
async def test_content_editor_ships(session, seeded):
    """Тип контента «Корабль»: создание, правка характеристик, удаление."""
    owner, _, cm = await _admins(session)

    catalog = await admin.list_content(session, cm)
    assert catalog["ships"] and catalog["factions"]  # стартовые корабли фракций

    # создание
    ship = await admin.create_content(session, owner, "ship", {
        "code": "test_barge", "name": "Тестовая баржа", "faction": "azimuth",
        "max_hull": 150, "fuel_capacity": 80, "attack": 3, "defense": 8,
        "module_slots": 4,
    })
    assert ship["max_hull"] == 150 and ship["faction"] == "azimuth"

    # правка
    updated = await admin.update_content(session, owner, "ship", "test_barge", {
        "name": "Баржа v2", "faction": "", "max_hull": 200, "fuel_capacity": 90,
        "attack": 4, "defense": 9, "module_slots": 5,
    })
    assert updated["name"] == "Баржа v2" and updated["faction"] == ""

    # валидация: несуществующая фракция, кривая прочность
    with pytest.raises(DomainError) as exc_info:
        await admin.create_content(session, owner, "ship", {
            "code": "bad_ship", "name": "X", "faction": "no_such_faction",
        })
    assert "фракция" in exc_info.value.message
    with pytest.raises(DomainError):
        await admin.update_content(session, owner, "ship", "test_barge", {
            "name": "X", "max_hull": 0,
        })

    # удаление: свободный корабль — можно; стартовый корабль игрока — нельзя
    result = await admin.delete_content(session, owner, "ship", "test_barge")
    assert result["mode"] == "hard"
    await _character(session, seeded)  # azimuth → владеет azimuth_probe
    with pytest.raises(DomainError) as exc_info:
        await admin.delete_content(session, owner, "ship", "azimuth_probe")
    assert "используется игроками" in exc_info.value.message


@requires_db
async def test_admin_faction_editor(session, seeded):
    """Редактор фракций: создание нейтральной, правка, удаление; занятая — защищена."""
    owner, _, cm = await _admins(session)

    created = await admin.create_content(session, cm, "faction", {
        "code": "test_guild", "name": "Тестовая гильдия", "kind": "neutral",
        "description": "Проверка", "bonuses": {"xp_bonus_pct": 3},
    })
    assert created["kind"] == "neutral" and created["bonuses"]["xp_bonus_pct"] == 3

    updated = await admin.update_content(session, owner, "faction", "test_guild", {
        "name": "Гильдия v2", "kind": "main", "bonuses": {},
    })
    assert updated["name"] == "Гильдия v2" and updated["kind"] == "main"

    # валидация: кривой вид фракции
    with pytest.raises(DomainError):
        await admin.update_content(session, owner, "faction", "test_guild", {
            "name": "X", "kind": "evil",
        })

    # пираты заняты врагами — удаление запрещено; тестовая — удаляется
    with pytest.raises(DomainError) as exc_info:
        await admin.delete_content(session, owner, "faction", "pirates")
    assert "используется" in exc_info.value.message
    result = await admin.delete_content(session, owner, "faction", "test_guild")
    assert result["mode"] == "hard"


@requires_db
async def test_config_sections_save_and_validate(session, seeded):
    """Вкладки конфигурации: чтение, сохранение, парные валидации, фракции."""
    owner, mod, _ = await _admins(session)

    data = await admin.get_config_sections(session, owner)
    ids = [s["id"] for s in data["sections"]]
    assert ids == ["character", "expedition", "reputation", "workshop", "combat", "economy", "factions"]
    with pytest.raises(DomainError) as exc_info:
        await admin.get_config_sections(session, mod)
    assert exc_info.value.code == "forbidden"

    character = next(s for s in data["sections"] if s["id"] == "character")
    values = {f["key"]: f["value"] for f in character["fields"]}
    assert values["inventory.base_size"] == 30

    # сохранение с изменением
    values["inventory.base_size"] = 35
    result = await admin.save_config_section(session, owner, "character", values)
    from app import stores

    assert await stores.get_param(session, "inventory.base_size") == 35

    # парная валидация: min > max
    values["names.min_length"] = 30
    values["names.max_length"] = 10
    with pytest.raises(DomainError) as exc_info:
        await admin.save_config_section(session, owner, "character", values)
    assert exc_info.value.code == "bad_config"

    # нечисловое значение
    values["names.min_length"] = "abc"
    with pytest.raises(DomainError):
        await admin.save_config_section(session, owner, "character", values)

    # фракции: меняем бонус азимута
    factions = next(s for s in data["sections"] if s["id"] == "factions")
    fvalues = {f["key"]: f["value"] for f in factions["fields"]}
    assert "azimuth.trade_fee_discount_pct" in fvalues
    fvalues["azimuth.trade_fee_discount_pct"] = 10
    result = await admin.save_config_section(session, owner, "factions", fvalues)
    factions2 = next(s for s in result["sections"] if s["id"] == "factions")
    updated = {f["key"]: f["value"] for f in factions2["fields"]}
    assert updated["azimuth.trade_fee_discount_pct"] == 10

    # неизвестная прибавка — ошибка
    with pytest.raises(DomainError):
        await admin.save_config_section(
            session, owner, "factions", {"azimuth.no_such_bonus": 5}
        )


@requires_db
async def test_content_editor_crud(session, seeded):
    """Редактор контента: создание/правка/удаление событий, заданий, рецептов."""
    owner, mod, cm = await _admins(session)

    # список: контент-менеджеру можно, модератору — нет
    catalog = await admin.list_content(session, cm)
    assert catalog["events"] and catalog["quests"] and catalog["recipes"]
    with pytest.raises(DomainError) as exc_info:
        await admin.list_content(session, mod)
    assert exc_info.value.code == "forbidden"

    # создание события (контент-менеджер — его зона)
    event = await admin.create_content(session, cm, "event", {
        "code": "test_event", "name": "Тестовое событие", "kind": "automatic",
        "fuel_cost": 2, "danger_min": 2, "danger_max": 4,
        "script": {"variants": [{"weight": 1, "outcome": {"message": "ок"}}]},
    })
    assert event["code"] == "test_event" and event["status"] == "published"

    # создание рецепта + валидация предмета результата
    recipe = await admin.create_content(session, owner, "recipe", {
        "code": "test_recipe", "name": "Тест-рецепт", "result_item_code": "scrap_metal",
        "materials": [{"code": "nano_patch", "qty": 1}], "duration_sec": 30,
    })
    assert recipe["result_qty"] == 1
    with pytest.raises(DomainError) as exc_info:
        await admin.create_content(session, owner, "recipe", {
            "code": "bad_recipe", "name": "X", "result_item_code": "no_such_item",
        })
    assert "не существует" in exc_info.value.message

    # валидация: interactive без choices; опасность наоборот; дубль кода
    for bad in (
        {"code": "e1", "name": "X", "kind": "interactive", "script": {}},
        {"code": "e2", "name": "X", "kind": "automatic", "danger_min": 5, "danger_max": 2,
         "script": {"variants": [{"weight": 1}]}},
        {"code": "test_event", "name": "Дубль", "kind": "automatic",
         "script": {"variants": [{"weight": 1}]}},
    ):
        with pytest.raises(DomainError) as exc_info:
            await admin.create_content(session, cm, "event", bad)
        assert exc_info.value.code == "bad_content"

    # правка задания
    updated = await admin.update_content(session, cm, "quest", "tutorial_1", {
        "name": "Первый вылет (обновлено)", "kind": "tutorial", "chain_order": 1,
        "objectives": [{"type": "expedition_start", "count": 1}],
        "rewards": {"quant": 200, "xp": 10},
    })
    assert updated["name"] == "Первый вылет (обновлено)"

    # удаление: без ссылок — жёсткое; задание с прогрессом игрока — архив
    result = await admin.delete_content(session, owner, "recipe", "test_recipe")
    assert result["mode"] == "hard"
    result = await admin.delete_content(session, owner, "event", "quiet_drift")
    assert result["mode"] == "hard"  # в тестовой БД истории по нему нет

    await _character(session, seeded)  # получает tutorial_1 автоматически
    result = await admin.delete_content(session, owner, "quest", "tutorial_1")
    assert result["mode"] == "archived"  # прогресс игроков не стираем
    catalog = await admin.list_content(session, owner)
    tutorial_1 = next(q for q in catalog["quests"] if q["code"] == "tutorial_1")
    assert tutorial_1["status"] == "archived"
    assert all(e["code"] != "quiet_drift" for e in catalog["events"])
    assert all(r["code"] != "test_recipe" for r in catalog["recipes"])

    # аудит
    actions = (
        await session.scalars(
            sa.select(AuditLog.action).where(AuditLog.target_id.in_(
                ["test_event", "test_recipe", "quiet_drift", "tutorial_1"]
            ))
        )
    ).all()
    assert {"create_content", "update_content", "delete_content"} <= set(actions)


@requires_db
async def test_server_load_and_audit_log_view(session, seeded):
    """Аналитика: нагрузка на сервер + журнал аудита (время МСК, ключ, суть)."""
    import re

    owner, mod, _ = await _admins(session)

    load = await admin.server_load(session, owner)
    assert load["cpu_cores"] >= 1
    assert load["db_size_mb"] > 0
    assert load["db_connections"] >= 1
    assert load["uptime_sec"] >= 0
    with pytest.raises(DomainError) as exc_info:
        await admin.server_load(session, mod)
    assert exc_info.value.code == "forbidden"

    # действие админа попадает в журнал: время МСК, ключ, суть
    await admin.set_config(session, owner, "inventory.base_size", 40)
    log = await admin.audit_log_view(session, owner)
    assert log["entries"]
    first = log["entries"][0]
    assert "owner (admin)" in first["actor"]
    assert "изменил конфиг" in first["summary"]
    assert "inventory.base_size" in first["summary"]
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}", first["time_msk"])
    with pytest.raises(DomainError) as exc_info:
        await admin.audit_log_view(session, mod)
    assert exc_info.value.code == "forbidden"


@requires_db
async def test_analytics_mvp(session, seeded):
    owner, _, _ = await _admins(session)
    await _character(session, seeded)
    data = await admin.analytics(session, owner)
    assert data["total_characters"] >= 1
    assert "tutorial_funnel" in data
