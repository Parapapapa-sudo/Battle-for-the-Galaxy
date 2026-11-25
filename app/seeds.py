"""Стартовый контент MVP (фракции, шаблоны кораблей, предметы).

ВНИМАНИЕ: числовые значения и состав — ЗАГЛУШКИ для разработки, требуют
согласования (docs/04-decisions.md, ТЗ «Минимальный состав контента MVP»).
Сиды идемпотентны (по code), можно запускать повторно.
"""
import asyncio
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.models_game import (
    EnemyAbility,
    EnemyTemplate,
    EventTable,
    EventTableEntry,
    EventTemplate,
    Faction,
    ItemTemplate,
    LootEntry,
    LootTable,
    ModuleTemplate,
    QuestTemplate,
    RecipeTemplate,
    ShipTemplate,
    ShopOffer,
)

log = structlog.get_logger(__name__)

# --- Заглушки предметов -------------------------------------------------------
ITEMS: list[dict[str, Any]] = [
    dict(
        code="scrap_metal",
        name="Металлолом",
        description="Рассыпчатый металл на переплавку. Основа крафта.",
        category="resource",
        rarity="common",
        stack_limit=50,
        losable_on_defeat=True,
    ),
    dict(
        code="trinium_ore",
        name="Триниевая руда",
        description="Редкий материал. Не валюта (ТЗ).",
        category="resource",
        rarity="rare",
        stack_limit=20,
        losable_on_defeat=False,
    ),
    dict(
        code="repair_kit",
        name="Ремкомплект",
        description="Устраняет поломку и возвращает максимальную прочность (ТЗ).",
        category="repair_kit",
        rarity="common",
        stack_limit=5,
        sellable=True,
        tradable=True,
        losable_on_defeat=False,
    ),
    dict(
        code="nano_patch",
        name="Нанопластырь",
        description="Боевой расходник: восстанавливает текущую прочность (ТЗ).",
        category="consumable",
        rarity="common",
        stack_limit=10,
        sellable=True,
        tradable=True,
        losable_on_defeat=True,
        params={"heal": 15},
    ),
]

# --- Заглушки фракций ---------------------------------------------------------
FACTIONS: list[dict[str, Any]] = [
    dict(
        code="azimuth",
        name="Консорциум «Азимут»",
        description="Торгово-исследовательский консорциум. Бонус-заглушка: торговля.",
        bonuses={"trade_fee_discount_pct": 5},
        start_ship="azimuth_probe",
        start_module="aim_computer",
    ),
    dict(
        code="helion",
        name="Хелионский Договор",
        description="Военно-флотский блок. Бонус-заглушка: прочность.",
        bonuses={"hull_bonus_pct": 5},
        start_ship="helion_interceptor",
        start_module="overclock_core",
    ),
    dict(
        code="cinder",
        name="Пепельный Картель",
        description="Пограничный картель. Бонус-заглушка: добыча.",
        bonuses={"loot_bonus_pct": 5},
        start_ship="cinder_raider",
        start_module="raider_coil",
    ),
    dict(
        code="lumen",
        name="Орден Люмена",
        description="Наследие старых миссий. Бонус-заглушка: опыт.",
        bonuses={"xp_bonus_pct": 5},
        start_ship="lumen_pilgrim",
        start_module="lumen_amplifier",
    ),
]


# --- Заглушки цепочки обучения (ТЗ: первое сюжетное задание обязательно) ---
QUESTS: list[dict[str, Any]] = [
    dict(
        code="tutorial_1",
        name="Первый вылет",
        description="Отправьтесь в экспедицию: launch, затем go.",
        kind="tutorial",
        chain_order=1,
        objectives=[{"type": "expedition_start", "count": 1}],
        rewards={"quant": 200, "xp": 10},
    ),
    dict(
        code="tutorial_2",
        name="Среди звёзд",
        description="Разберитесь с событиями экспедиции (go, choice/decline).",
        kind="tutorial",
        chain_order=2,
        objectives=[{"type": "event_resolved", "count": 2}],
        rewards={"items": [{"code": "nano_patch", "qty": 2}], "xp": 15},
    ),
    dict(
        code="tutorial_3",
        name="Первый контакт",
        description="Победите в пошаговом бою (attack, при необходимости — retreat).",
        kind="tutorial",
        chain_order=3,
        objectives=[{"type": "battle_won", "count": 1}],
        rewards={"quant": 300, "xp": 25},
    ),
    dict(
        code="story_1",
        name="Своя орбита",
        description="Скрафтьте предмет в космопорту: recipes, craft.",
        kind="story",
        chain_order=4,
        objectives=[{"type": "crafted", "count": 1}],
        rewards={"quant": 500, "xp": 40},
    ),
]


# --- Заглушки магазина (подписки 7/30 — ТЗ; цены-заглушки) ---
SHOP_OFFERS: list[dict[str, Any]] = [
    dict(code="sub_7", kind="subscription", price_eon=99, payload={"kind": "sub_7"}),
    dict(code="sub_30", kind="subscription", price_eon=249, payload={"kind": "sub_30"}),
    dict(code="eon_pack_100", kind="pack", price_eon=0, price_rub=100, payload={"eons": 100}),
    dict(code="eon_pack_500", kind="pack", price_eon=0, price_rub=500, payload={"eons": 500}),
    dict(code="eon_pack_1000", kind="pack", price_eon=0, price_rub=1000, payload={"eons": 1000}),
]


# --- Заглушки рецептов крафта ---
RECIPES: list[dict[str, Any]] = [
    dict(
        code="craft_nano_patch",
        name="Нанопластырь",
        result_item_code="nano_patch",
        result_qty=1,
        materials=[{"code": "scrap_metal", "qty": 3}],
        duration_sec=45,
        cancel_return_pct=50,
    ),
    dict(
        code="craft_repair_kit",
        name="Ремкомплект",
        result_item_code="repair_kit",
        result_qty=1,
        materials=[{"code": "scrap_metal", "qty": 5}],
        duration_sec=90,
        cancel_return_pct=50,
    ),
    dict(
        code="craft_trinium_refine",
        name="Переработка триния",
        result_item_code="trinium_ore",
        result_qty=1,
        materials=[{"code": "scrap_metal", "qty": 8}, {"code": "nano_patch", "qty": 1}],
        duration_sec=300,
        cancel_return_pct=50,
    ),
]


# --- Заглушки противников (ТЗ: категории normal/dangerous/elite/boss; характеристики
# одинаковы для всех фракций; пассивы/слабости — только elite/boss) ---
ENEMY_ABILITIES: list[dict[str, Any]] = [
    dict(
        code="railgun_burst",
        name="Залп рейлгана",
        params={"damage_mult": 1.6, "cooldown": 3, "chance": 0.4},
    ),
]

ENEMIES: list[dict[str, Any]] = [
    dict(
        code="pirate_scout",
        category="normal",
        name="Пират-скаут",
        stats={"hull": 40, "damage": 8, "xp": 15},
        abilities=[],
        passives={},
        weaknesses={},
        loot_code="pirate_scout_loot",
    ),
    dict(
        code="pirate_raider",
        category="dangerous",
        name="Пират-рейдер",
        stats={"hull": 70, "damage": 12, "xp": 30},
        abilities=["railgun_burst"],
        passives={},
        weaknesses={},
        loot_code="pirate_raider_loot",
    ),
    dict(
        code="pirate_captain",
        category="elite",
        name="Пиратский капитан",
        stats={"hull": 120, "damage": 16, "xp": 60},
        abilities=["railgun_burst"],
        passives={"damage_reduction_pct": 20},
        weaknesses={"bonus_damage_taken_pct": 25},
        loot_code="pirate_captain_loot",
    ),
]

LOOT_TABLES: dict[str, list[dict[str, Any]]] = {
    "pirate_scout_loot": [
        dict(item="scrap_metal", weight=10, qty_min=8, qty_max=12, chance=1.0),
    ],
    "pirate_raider_loot": [
        dict(item="scrap_metal", weight=10, qty_min=12, qty_max=20, chance=1.0),
        dict(item="nano_patch", weight=4, qty_min=1, qty_max=2, chance=0.5),
    ],
    "pirate_captain_loot": [
        dict(item="scrap_metal", weight=10, qty_min=20, qty_max=30, chance=1.0),
        dict(item="repair_kit", weight=6, qty_min=1, qty_max=1, chance=0.6),
        dict(item="trinium_ore", weight=2, qty_min=1, qty_max=2, chance=0.4),
    ],
}

# --- Заглушки модулей (ТЗ: способности активные, навыки пассивные) ---
MODULES: list[dict[str, Any]] = [
    dict(
        code="aim_computer",
        name="Навигационный вычислитель",
        kind="skill",
        faction="azimuth",
        params={"attack_bonus": {"base": 2, "per_level": 1}},
        max_per_ship=2,
    ),
    dict(
        code="overclock_core",
        name="Разогнанное ядро",
        kind="ability",
        faction="helion",
        params={"ability": {"combat_cmd": "shot", "name": "Мощный выстрел", "damage_mult": 2.0, "cooldown": 3}},
        max_per_ship=1,
    ),
    dict(
        code="raider_coil",
        name="Катушка налётчика",
        kind="skill",
        faction="cinder",
        params={"attack_bonus": {"base": 1, "per_level": 2}},
        max_per_ship=2,
    ),
    dict(
        code="lumen_amplifier",
        name="Люмен-усилитель",
        kind="ability",
        faction="lumen",
        params={"ability": {"combat_cmd": "flare", "name": "Луч-призрак", "damage_mult": 1.6, "cooldown": 2}},
        max_per_ship=1,
    ),
]

# Стартовые корабли: характеристики — ЗАГЛУШКИ (открытый вопрос баланса ТЗ №3)
_START_SHIP_STATS = {"max_hull": 100, "fuel_capacity": 50, "attack": 5, "defense": 5}

_START_ITEMS = [
    {"code": "scrap_metal", "qty": 10},
    {"code": "repair_kit", "qty": 2},
    {"code": "nano_patch", "qty": 3},
]

# --- Заглушки событий экспедиций (ТЗ: минимальный состав контента — на согласовании) ---
EVENT_TABLE_CODE = "standard"

EVENTS: list[dict[str, Any]] = [
    dict(
        code="quiet_drift",
        name="Тихий дрейф",
        description="Пустота и звёзды. Никаких происшествий.",
        kind="automatic",
        fuel_cost=1,
        danger_min=1,
        danger_max=1,
        weight=6,
        script={"variants": [{"message": "Спокойный участок пути. Ничего не произошло."}]},
    ),
    dict(
        code="derelict_probe",
        name="Брошенный зонд",
        description="На сканерах — старый исследовательский зонд.",
        kind="interactive",
        fuel_cost=2,
        danger_min=2,
        danger_max=4,
        weight=10,
        script={
            "choices": [
                {
                    "key": "1",
                    "text": "Разобрать на запчасти",
                    "outcome": {"message": "Зонд разобран.", "grants": [{"code": "scrap_metal", "qty": 8}]},
                },
                {
                    "key": "2",
                    "text": "Снять данные и уйти",
                    "outcome": {"message": "Данные продадутся на аукционе.", "grants": [{"code": "scrap_metal", "qty": 2}]},
                },
            ]
        },
    ),
    dict(
        code="asteroid_field",
        name="Поле астероидов",
        description="Плотное кольцо обломков — богатый участок и риск для корпуса.",
        kind="automatic",
        fuel_cost=3,
        danger_min=4,
        danger_max=8,
        weight=10,
        script={
            "variants": [
                {
                    "chance": 0.6,
                    "message": "Удачная разведка жилы.",
                    "grants": [{"code": "scrap_metal", "qty": 12}],
                },
                {
                    "message": "Сколы камней повредили корпус.",
                    "grants": [{"code": "scrap_metal", "qty": 5}],
                    "hull_damage": 8,
                },
            ]
        },
    ),
    dict(
        code="distress_beacon",
        name="Сигнал бедствия",
        description="Кто-то просит помощи на границе сектора.",
        kind="interactive",
        fuel_cost=2,
        danger_min=3,
        danger_max=7,
        weight=8,
        script={
            "choices": [
                {
                    "key": "1",
                    "text": "Откликнуться на сигнал",
                    "variants": [
                        {
                            "chance": 0.5,
                            "message": "Это была ловушка пиратов!",
                            "hull_damage": 15,
                        },
                        {
                            "message": "Спасённый экипаж отблагодарил вас.",
                            "grants": [{"code": "repair_kit", "qty": 1}],
                        },
                    ],
                },
                {
                    "key": "2",
                    "text": "Игнорировать",
                    "outcome": {"message": "Вы прошли мимо."},
                },
            ]
        },
    ),
    dict(
        code="solar_storm",
        name="Солнечная буря",
        description="Всплеск звёздной активности накрывает курс.",
        kind="automatic",
        fuel_cost=1,
        danger_min=5,
        danger_max=9,
        weight=8,
        script={
            "variants": [{"message": "Буря потрепала щиты и корпус.", "hull_damage": 10}]
        },
    ),
    dict(
        code="trinium_vein",
        name="Триниевая жила",
        description="Редчайшее месторождение триния прямо по курсу.",
        kind="automatic",
        fuel_cost=4,
        danger_min=6,
        danger_max=10,
        weight=5,
        unique=True,
        script={
            "variants": [{"message": "Добыта триниевая руда.", "grants": [{"code": "trinium_ore", "qty": 3}]}]
        },
    ),
    dict(
        code="pirate_ambush",
        name="Засада пиратов",
        description="Скиттеры пиратов перехватывают ваш курс.",
        kind="interactive",
        fuel_cost=3,
        danger_min=4,
        danger_max=8,
        weight=8,
        script={
            "choices": [
                {
                    "key": "1",
                    "text": "Принять бой",
                    "outcome": {"message": "Пираты идут на абордаж!", "battle": {"enemy": "pirate_scout"}},
                },
                {
                    "key": "2",
                    "text": "Уйти с курса",
                    "outcome": {"message": "Вы ушли от засады, потратив топливо на манёвр."},
                },
            ]
        },
    ),
    dict(
        code="pirate_captain_hunt",
        name="Охота капитана",
        description="Легендарный пиратский капитан объявил награду за ваш корабль.",
        kind="interactive",
        fuel_cost=3,
        danger_min=8,
        danger_max=10,
        weight=3,
        unique=True,
        script={
            "choices": [
                {
                    "key": "1",
                    "text": "Дать бой",
                    "outcome": {"message": "Капитан выходит на орбиту перехвата!", "battle": {"enemy": "pirate_captain"}},
                },
                {
                    "key": "2",
                    "text": "Скрыться в поясе обломков",
                    "outcome": {"message": "Вы ушли от погони."},
                },
            ]
        },
    ),
]


async def ensure_seeds(session: AsyncSession) -> dict[str, int]:
    """Идемпотентно создаёт контент-заглушки. Возвращает число созданных записей."""
    created = {"items": 0, "factions": 0, "ships": 0, "events": 0, "event_tables": 0,
               "enemies": 0, "enemy_abilities": 0, "loot_tables": 0, "modules": 0,
               "recipes": 0, "shop_offers": 0, "quests": 0}

    existing_items = set(
        (await session.scalars(sa.select(ItemTemplate.code))).all()
    )
    for spec in ITEMS:
        if spec["code"] not in existing_items:
            session.add(ItemTemplate(**spec))
            created["items"] += 1

    existing_factions = set((await session.scalars(sa.select(Faction.code))).all())
    faction_rows: dict[str, Faction] = {}
    for spec in FACTIONS:
        start_resources = {
            "ship_template": spec["start_ship"],
            "items": _START_ITEMS,
            "modules": [spec["start_module"]],
        }
        if spec["code"] in existing_factions:
            row = await session.scalar(
                sa.select(Faction).where(Faction.code == spec["code"])
            )
            assert row is not None
            # стартовый набор фракции актуализируем (сиды — авторитет для dev-контента)
            if (row.start_resources or {}).get("modules") != [spec["start_module"]]:
                row.start_resources = start_resources
            faction_rows[spec["code"]] = row
            continue
        faction = Faction(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            bonuses=spec["bonuses"],
            start_resources=start_resources,
        )
        session.add(faction)
        faction_rows[spec["code"]] = faction
        created["factions"] += 1

    await session.flush()  # чтобы у заглушек-фракций появились id

    # --- модули (способности/навыки) ---
    for spec in MODULES:
        existing = await session.scalar(
            sa.select(ModuleTemplate).where(ModuleTemplate.code == spec["code"])
        )
        if existing is None:
            session.add(
                ModuleTemplate(
                    code=spec["code"],
                    name=spec["name"],
                    kind=spec["kind"],
                    faction_id=faction_rows[spec["faction"]].id,
                    params=spec["params"],
                    max_per_ship=spec["max_per_ship"],
                )
            )
            created["modules"] += 1

    existing_ships = set((await session.scalars(sa.select(ShipTemplate.code))).all())
    for spec in FACTIONS:
        ship_code = spec["start_ship"]
        if ship_code in existing_ships:
            continue
        session.add(
            ShipTemplate(
                code=ship_code,
                faction_id=faction_rows[spec["code"]].id,
                name=f"Шлюп «{spec['name'].split(' ')[0]}»",
                base_stats=dict(_START_SHIP_STATS),
                module_slots=3,
            )
        )
        created["ships"] += 1

    # --- события экспедиций и таблица генерации ---
    event_rows: dict[str, EventTemplate] = {}
    for spec in EVENTS:
        code = spec["code"]
        event_row = event_rows.get(code)
        if event_row is None:
            existing = await session.scalar(
                sa.select(EventTemplate).where(EventTemplate.code == code)
            )
            if existing is not None:
                event_row = existing
            else:
                event_row = EventTemplate(
                    code=code,
                    name=spec["name"],
                    description=spec["description"],
                    kind=spec["kind"],
                    fuel_cost=spec["fuel_cost"],
                    danger_min=spec["danger_min"],
                    danger_max=spec["danger_max"],
                    script=spec["script"],
                    is_unique_per_expedition=spec.get("unique", False),
                    can_refuse=spec.get("can_refuse", True),
                )
                session.add(event_row)
                created["events"] += 1
            event_rows[code] = event_row
    await session.flush()

    table = await session.scalar(
        sa.select(EventTable).where(EventTable.code == EVENT_TABLE_CODE)
    )
    if table is None:
        table = EventTable(code=EVENT_TABLE_CODE, name="Стандартная таблица экспедиций")
        session.add(table)
        await session.flush()
        created["event_tables"] = 1
    linked = set(
        (
            await session.scalars(
                sa.select(EventTableEntry.event_template_id).where(
                    EventTableEntry.table_id == table.id
                )
            )
        ).all()
    )
    for spec in EVENTS:
        event_row = event_rows[spec["code"]]
        if event_row.id not in linked:
            session.add(
                EventTableEntry(
                    table_id=table.id, event_template_id=event_row.id, weight=spec["weight"]
                )
            )

    # --- противники, способности, таблицы лута ---
    for spec in ENEMY_ABILITIES:
        existing = await session.scalar(
            sa.select(EnemyAbility).where(EnemyAbility.code == spec["code"])
        )
        if existing is None:
            session.add(EnemyAbility(code=spec["code"], name=spec["name"], params=spec["params"]))
            created["enemy_abilities"] += 1

    item_ids: dict[str, Any] = {
        r.code: r.id for r in (await session.scalars(sa.select(ItemTemplate))).all()
    }

    loot_table_ids: dict[str, Any] = {}
    for table_code, entries in LOOT_TABLES.items():
        loot_table = await session.scalar(
            sa.select(LootTable).where(LootTable.code == table_code)
        )
        if loot_table is None:
            loot_table = LootTable(code=table_code)
            session.add(loot_table)
            await session.flush()
            created["loot_tables"] += 1
            for entry in entries:
                session.add(
                    LootEntry(
                        table_id=loot_table.id,
                        item_template_id=item_ids[entry["item"]],
                        weight=entry["weight"],
                        qty_min=entry["qty_min"],
                        qty_max=entry["qty_max"],
                        chance=entry["chance"],
                    )
                )
        loot_table_ids[table_code] = loot_table.id
    await session.flush()

    for spec in ENEMIES:
        existing = await session.scalar(
            sa.select(EnemyTemplate).where(EnemyTemplate.code == spec["code"])
        )
        if existing is None:
            session.add(
                EnemyTemplate(
                    code=spec["code"],
                    category=spec["category"],
                    name=spec["name"],
                    stats=spec["stats"],
                    abilities=spec["abilities"],
                    passives=spec["passives"],
                    weaknesses=spec["weaknesses"],
                    loot_table_id=loot_table_ids[spec["loot_code"]],
                )
            )
            created["enemies"] += 1

    # --- рецепты крафта ---
    for spec in RECIPES:
        existing = await session.scalar(
            sa.select(RecipeTemplate).where(RecipeTemplate.code == spec["code"])
        )
        if existing is None:
            session.add(
                RecipeTemplate(
                    code=spec["code"],
                    name=spec["name"],
                    result_item_code=spec["result_item_code"],
                    result_qty=spec["result_qty"],
                    materials=spec["materials"],
                    duration_sec=spec["duration_sec"],
                    cancel_return_pct=spec["cancel_return_pct"],
                )
            )
            created["recipes"] += 1

    # --- цепочка обучения ---
    for spec in QUESTS:
        existing = await session.scalar(
            sa.select(QuestTemplate).where(QuestTemplate.code == spec["code"])
        )
        if existing is None:
            session.add(
                QuestTemplate(
                    code=spec["code"],
                    name=spec["name"],
                    description=spec["description"],
                    kind=spec["kind"],
                    chain_order=spec["chain_order"],
                    objectives=spec["objectives"],
                    rewards=spec["rewards"],
                )
            )
            created["quests"] += 1

    # --- магазин ---
    for spec in SHOP_OFFERS:
        existing = await session.scalar(
            sa.select(ShopOffer).where(ShopOffer.code == spec["code"])
        )
        if existing is not None:
            # актуализация цены паков (сиды — авторитет для dev-контента)
            if spec.get("price_rub") and existing.price_rub != spec["price_rub"]:
                existing.price_rub = spec["price_rub"]
            continue
        if existing is None:
            session.add(
                ShopOffer(
                    code=spec["code"],
                    kind=spec["kind"],
                    price_eon=spec["price_eon"],
                    price_rub=spec.get("price_rub"),
                    payload=spec["payload"],
                )
            )
            created["shop_offers"] += 1

    await session.flush()
    log.info("seeds_ensured", **created)
    return created


async def main() -> None:
    from app.config import get_settings
    from app.logging import configure_logging

    configure_logging(get_settings().log_level)
    async with session_factory() as session, session.begin():
        created = await ensure_seeds(session)
    print(f"Сиды применены: {created}")


if __name__ == "__main__":
    asyncio.run(main())
