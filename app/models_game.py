"""Игровые модели этапа 1: аккаунты, персонажи, фракции, корабли, инвентарь.

Модель данных: docs/02-data-model.md (разделы 2–5).
"""
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, new_uuid

# JSONB, отслеживающий изменения по месту (state["k"]=v) — иначе мутеры теряются.
MutDict = MutableDict.as_mutable(JSONB)
MutList = MutableList.as_mutable(JSONB)


class Account(Base, TimestampMixin):
    """Единый игровой аккаунт (прогресс не привязан к платформе — ТЗ)."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|banned
    ban_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AccountIdentity(Base, TimestampMixin):
    """Привязка платформы (VK/Telegram/console) к аккаунту."""

    __tablename__ = "account_identities"
    __table_args__ = (UniqueConstraint("platform", "platform_user_id", name="uq_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # vk|telegram|console
    platform_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    link_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Faction(Base, TimestampMixin):
    __tablename__ = "factions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    bonuses: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    # {"ship_template": code, "items": [{"code": ..., "qty": ...}]}
    start_resources: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)


class Character(Base, TimestampMixin):
    """Имя может повторяться (ТЗ); уникален id. Удаление игроком не предусмотрено."""

    __tablename__ = "characters"
    __table_args__ = (
        Index("ix_characters_account_active", "account_id", unique=True, postgresql_where=sa.text("status = 'active'")),
        Index("ix_characters_name_norm", "name_normalized"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(64), nullable=False)
    faction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("factions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|blocked
    tutorial_state: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    craft_slots: Mapped[int] = mapped_column(default=1)
    inventory_size: Mapped[int] = mapped_column(default=30)  # ТЗ: база 30, +50% подписка
    warehouse_size: Mapped[int] = mapped_column(default=20)
    faction_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    xp_pool: Mapped[int] = mapped_column(BigInteger, default=0)
    build_resets: Mapped[int] = mapped_column(default=0)  # число сбросов сборки (стоимость растёт)


class Reputation(Base):
    """Индивидуальная репутация отделена от отношений фракций (ТЗ).

    Одна активная репутация на персонажа (частичный уникальный индекс).
    При смене фракции старая сохраняется is_active=false, новая — отрицательный старт.
    """

    __tablename__ = "reputations"
    __table_args__ = (
        Index(
            "uq_reputation_active",
            "character_id",
            unique=True,
            postgresql_where=sa.text("is_active"),
        ),
    )

    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    faction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("factions.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CharacterTitle(Base):
    """Особые звания за достижения — сохраняются при смене фракции (ТЗ)."""

    __tablename__ = "character_titles"

    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    title_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # null = система


class ShipTemplate(Base, TimestampMixin):
    __tablename__ = "ship_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    faction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("factions.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128))
    base_stats: Mapped[dict[str, Any]] = mapped_column(
        MutDict, default=dict
    )  # max_hull, fuel_capacity, attack, defense
    module_slots: Mapped[int] = mapped_column(default=3)


class Ship(Base, TimestampMixin):
    """Корабль не уничтожается окончательно (ТЗ). Макс. прочность = f(base, поломки) — вычисляется."""

    __tablename__ = "ships"

    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ship_templates.id", ondelete="RESTRICT"), nullable=False
    )
    base_max_hull: Mapped[int] = mapped_column(nullable=False)
    current_hull: Mapped[int] = mapped_column(nullable=False)
    malfunctions: Mapped[int] = mapped_column(nullable=False, default=0)
    fuel: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), default="dock")  # dock|expedition|returning|battle


class ItemTemplate(Base, TimestampMixin):
    """Флаги sellable/tradable независимы (ТЗ); потеря при поражении — по флагу."""

    __tablename__ = "item_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(
        String(32), default="resource"
    )  # resource|consumable|module|repair_kit|special
    rarity: Mapped[str] = mapped_column(String(16), default="common")  # common|rare|epic|unique
    stack_limit: Mapped[int] = mapped_column(default=1)  # индивидуальный лимит стека (ТЗ)
    is_unique: Mapped[bool] = mapped_column(default=False)  # нельзя получить повторно/продать
    sellable: Mapped[bool] = mapped_column(default=True)
    tradable: Mapped[bool] = mapped_column(default=True)
    losable_on_defeat: Mapped[bool] = mapped_column(default=False)
    params: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)  # {"heal": 15} и т.п.


class Inventory(Base, TimestampMixin):
    """kind: ship (инвентарь) | warehouse (склад космопорта) | waiting (безлимитный, ТЗ)."""

    __tablename__ = "inventories"
    __table_args__ = (UniqueConstraint("character_id", "kind", name="uq_inventory_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    size_limit: Mapped[int | None] = mapped_column(nullable=True)  # NULL = безлимит (waiting)


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"
    __table_args__ = (Index("ix_inv_items_inventory", "inventory_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    inventory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventories.id", ondelete="CASCADE"), nullable=False
    )
    item_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item_templates.id", ondelete="RESTRICT"), nullable=False
    )
    qty: Mapped[int] = mapped_column(nullable=False, default=1)  # уникальные: qty=1
    module_instance_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # этап 5
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ModerationName(Base):
    """Журнал проверки имён персонажей (ТЗ: оскорбления, запрещённые символы, имитация админов)."""

    __tablename__ = "moderation_names"

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # allowed|rejected
    rule_hit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    moderator_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventTemplate(Base, TimestampMixin):
    """Событие экспедиции. script — JSON: choices/variants с outcome
    (граф узлов для будущего конструктора механик — docs/01-architecture.md §5.2)."""

    __tablename__ = "event_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16))  # interactive|automatic
    fuel_cost: Mapped[int] = mapped_column(default=1)
    danger_min: Mapped[int] = mapped_column(default=1)
    danger_max: Mapped[int] = mapped_column(default=1)
    conditions: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    script: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    is_unique_per_expedition: Mapped[bool] = mapped_column(default=False)
    can_refuse: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(16), default="published")


class EventTable(Base, TimestampMixin):
    __tablename__ = "event_tables"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))


class EventTableEntry(Base):
    __tablename__ = "event_table_entries"

    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event_tables.id", ondelete="CASCADE"), primary_key=True
    )
    event_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event_templates.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[int] = mapped_column(default=10)
    conditions: Mapped[dict[str, Any] | None] = mapped_column(MutDict, nullable=True)


class Expedition(Base, TimestampMixin):
    """Экспедиция бесконечна по структуре (ТЗ): очередь пополняется по мере прохождения.

    Офлайн не тикает — прогресс только по командам игрока (нет фонового тикера).
    """

    __tablename__ = "expeditions"
    __table_args__ = (
        Index(
            "uq_expedition_ongoing",
            "character_id",
            unique=True,
            postgresql_where=sa.text("status IN ('active', 'returning')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active"
    )  # active|returning|completed|aborted_defeat|aborted_fuel
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fuel_at_start: Mapped[int] = mapped_column(default=0)
    fuel_remaining: Mapped[int] = mapped_column(default=0)
    depth: Mapped[int] = mapped_column(default=0)
    event_queue: Mapped[list[Any]] = mapped_column(MutList, default=list)  # [коды шаблонов]
    queue_pos: Mapped[int] = mapped_column(default=0)  # индекс следующего к созданию события
    return_reason: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # voluntary|fuel_empty|defeat
    return_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExpeditionEvent(Base, TimestampMixin):
    """Экспозиция события в экспедиции. Завершённое закрыто окончательно (ТЗ)."""

    __tablename__ = "expedition_events"
    __table_args__ = (
        UniqueConstraint("expedition_id", "seq", name="uq_exp_event_seq"),
        Index("ix_exp_events_expedition", "expedition_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    expedition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expeditions.id", ondelete="CASCADE"), nullable=False
    )
    event_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event_templates.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offered")  # offered|resolved|declined
    snapshot: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)  # снапшот шаблона
    resolution: Mapped[dict[str, Any] | None] = mapped_column(MutDict, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EnemyAbility(Base, TimestampMixin):
    """Активные способности противников (простая логика в MVP)."""

    __tablename__ = "enemy_abilities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict[str, Any]] = mapped_column(
        MutDict, default=dict
    )  # damage_mult, cooldown, chance


class LootTable(Base, TimestampMixin):
    __tablename__ = "loot_tables"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)


class LootEntry(Base):
    __tablename__ = "loot_entries"
    __table_args__ = (Index("ix_loot_entries_table", "table_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loot_tables.id", ondelete="CASCADE"), nullable=False
    )
    item_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item_templates.id", ondelete="CASCADE"), nullable=False
    )
    weight: Mapped[int] = mapped_column(default=10)
    qty_min: Mapped[int] = mapped_column(default=1)
    qty_max: Mapped[int] = mapped_column(default=1)
    chance: Mapped[float] = mapped_column(default=1.0)


class EnemyTemplate(Base, TimestampMixin):
    """Характеристики одинаковы для всех фракций (ТЗ).

    Пассивы и слабости — только у elite/boss (ТЗ); в MVP проверяется сид-контентом,
    ограничение на уровне контент-процесса (админ-UI, этап 9).
    """

    __tablename__ = "enemy_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(16))  # normal|dangerous|elite|boss
    name: Mapped[str] = mapped_column(String(128))
    stats: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)  # hull, damage, xp
    abilities: Mapped[list[Any]] = mapped_column(MutList, default=list)  # коды enemy_abilities
    passives: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    weaknesses: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    loot_table_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("loot_tables.id", ondelete="SET NULL"), nullable=True
    )
    behavior: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")


class Battle(Base, TimestampMixin):
    """Пошаговой бой: state machine в JSONB; ход противника НЕ исполняется автоматически
    при бездействии игрока (ТЗ) — таймеров в бою нет."""

    __tablename__ = "battles"
    __table_args__ = (
        Index(
            "uq_battle_active",
            "character_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    expedition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expeditions.id", ondelete="SET NULL"), nullable=True
    )
    enemy_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enemy_templates.id", ondelete="RESTRICT"), nullable=False
    )
    enemy_snapshot: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    player_snapshot: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)  # enemy_hull, turn_no, cooldowns, consumables_used_turn
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|won|lost|retreated
    retreat_used: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BattleTurn(Base):
    """Журнал ходов: краткий итог + подробный лог (ТЗ). Append-only."""

    __tablename__ = "battle_turns"
    __table_args__ = (Index("ix_battle_turns_battle", "battle_id", "turn_no"),)

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    battle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("battles.id", ondelete="CASCADE"), nullable=False
    )
    turn_no: Mapped[int] = mapped_column(nullable=False)
    actor: Mapped[str] = mapped_column(String(16))  # player|enemy
    action: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    log: Mapped[list[Any]] = mapped_column(MutList, default=list)


class ModuleTemplate(Base, TimestampMixin):
    """Устанавливаемый на корабль модуль: способности активные, навыки пассивные (ТЗ).

    Полученный модуль сразу доступен к установке, изучение не требуется (ТЗ).
    """

    __tablename__ = "module_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16))  # ability|skill
    faction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("factions.id", ondelete="SET NULL"), nullable=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    max_per_ship: Mapped[int] = mapped_column(default=1)  # настройка модуля (ТЗ)
    in_combat_restrictions: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")


class ModuleInstance(Base, TimestampMixin):
    """Экземпляр модуля у персонажа. Снятие не теряет прогресс (ТЗ)."""

    __tablename__ = "module_instances"
    __table_args__ = (
        Index("ix_module_instances_char", "character_id"),
        Index("ix_module_instances_slot", "character_id", "installed_slot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("module_templates.id", ondelete="RESTRICT"), nullable=False
    )
    xp_invested: Mapped[int] = mapped_column(default=0)
    installed_slot: Mapped[int | None] = mapped_column(nullable=True)  # NULL = в инвентаре


class RecipeTemplate(Base, TimestampMixin):
    """Рецепт крафта. materials — [{"code", "qty"}] по кодам предметов."""

    __tablename__ = "recipe_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    result_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    result_qty: Mapped[int] = mapped_column(default=1)
    materials: Mapped[list[Any]] = mapped_column(MutList, default=list)
    duration_sec: Mapped[int] = mapped_column(default=60)
    cancel_return_pct: Mapped[int] = mapped_column(default=50)
    requirements: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")


class CraftJob(Base, TimestampMixin):
    """Заказ крафта. Таймер идёт в реальном времени, даже офлайн (ТЗ)."""

    __tablename__ = "craft_jobs"
    __table_args__ = (
        Index("ix_craft_jobs_char", "character_id", "status"),
        Index("ix_craft_jobs_due", "status", "finishes_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipe_templates.id", ondelete="RESTRICT"), nullable=False
    )
    slot_no: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finishes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|cancelled


# --- экономика (этап 7) ------------------------------------------------------------


class Wallet(Base):
    """Кошелёк: balance (доступно) + reserved (зарезервировано под сделки). Триний — предмет."""

    __tablename__ = "wallets"
    __table_args__ = (sa.PrimaryKeyConstraint("character_id", "currency", name="pk_wallets"),)

    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)  # eon|quant
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)


class WalletLedger(Base, TimestampMixin):
    """Append-only журнал движений — единственный путь изменения кошелька (ТЗ)."""

    __tablename__ = "wallet_ledger"
    __table_args__ = (
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_idem"),
        Index("ix_ledger_char", "character_id", "currency", "id"),
        Index("ix_ledger_ref", "ref_type", "ref_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    character_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="system")


class Transfer(Base, TimestampMixin):
    """Прямая передача (только космопорт — ТЗ). Ценные — с подтверждением получателя."""

    __tablename__ = "transfers"
    __table_args__ = (
        Index("ix_transfers_to", "to_id", "status"),
        Index("ix_transfers_expiry", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    from_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    to_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), default="pending_confirmation"
    )  # pending_confirmation|active|completed|expired|rejected|cancelled
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuctionLot(Base, TimestampMixin):
    __tablename__ = "auction_lots"
    __table_args__ = (Index("ix_lots_active", "status", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(default=1)
    min_price_quant: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buyout_quant: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    top_bid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    top_bidder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default="active"
    )  # active|sold|expired|cancelled_pending_return
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuctionBid(Base):
    __tablename__ = "auction_bids"

    id: Mapped[int] = mapped_column(BigInteger, sa.Identity(), primary_key=True)
    lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auction_lots.id", ondelete="CASCADE"), nullable=False
    )
    bidder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(default=True)


class EonMarketOrder(Base, TimestampMixin):
    """Ордерная книга внутреннего рынка Эонов (спред/курс — config, защита отклонением)."""

    __tablename__ = "eon_market_orders"
    __table_args__ = (Index("ix_eon_orders", "status", "side", "price_quant"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # buy|sell
    amount_eon: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filled_eon: Mapped[int] = mapped_column(BigInteger, default=0)
    price_quant: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open"
    )  # open|partial|matched|cancelled


class ShopOffer(Base):
    __tablename__ = "shop_offers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # pack|subscription
    price_eon: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")


class Purchase(Base, TimestampMixin):
    """Платёж. Идемпотентность webhook — UNIQUE(provider, provider_payment_id) (ТЗ)."""

    __tablename__ = "purchases"
    __table_args__ = (sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shop_offers.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|paid|refunded|failed
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_char", "character_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # sub_7|sub_30
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    source_purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchases.id", ondelete="SET NULL"), nullable=True
    )


# --- задания и фракционные события (этап 8) -----------------------------------------


class QuestTemplate(Base, TimestampMixin):
    """Задание цепочки (обучение/сюжет). Первое сюжетное обязательно (ТЗ)."""

    __tablename__ = "quest_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    faction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("factions.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="tutorial")
    chain_order: Mapped[int] = mapped_column(default=0)
    objectives: Mapped[list[Any]] = mapped_column(MutList, default=list)
    rewards: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")


class CharacterQuest(Base):
    __tablename__ = "character_quests"

    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    quest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quest_templates.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed
    progress: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)  # {"0": count}
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FactionEvent(Base, TimestampMixin):
    """Общая цель фракций с индивидуальным вкладом (ТЗ). Запуск: schedule/вручную."""

    __tablename__ = "faction_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="scheduled"
    )  # scheduled|active|finished|cancelled
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FactionEventParticipant(Base):
    __tablename__ = "faction_event_participants"

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("faction_events.id", ondelete="CASCADE"), primary_key=True
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    contribution: Mapped[int] = mapped_column(default=0)
    rewards_granted: Mapped[bool] = mapped_column(default=False)


# --- админ-панель (этап 9) -----------------------------------------------------------


class AdminUser(Base, TimestampMixin):
    """Роли (ТЗ): admin — всё; moderator — баны/эффекты/просмотр; content_manager — своя зона."""

    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))  # admin|moderator|content_manager
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EffectTemplate(Base, TimestampMixin):
    """Белый список эффектов — модератор применяет только их (ТЗ)."""

    __tablename__ = "effect_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(16), default="character")
    params_schema: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    duration_default_sec: Mapped[int | None] = mapped_column(nullable=True)


class CharacterEffect(Base, TimestampMixin):
    __tablename__ = "character_effects"
    __table_args__ = (Index("ix_character_effects_char", "character_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    effect_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("effect_templates.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(16), default="moderator")
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(MutDict, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "Account",
    "AccountIdentity",
    "AdminUser",
    "AuctionBid",
    "AuctionLot",
    "Battle",
    "BattleTurn",
    "Character",
    "CharacterEffect",
    "CharacterQuest",
    "EffectTemplate",
    "CraftJob",
    "EonMarketOrder",
    "FactionEvent",
    "FactionEventParticipant",
    "Purchase",
    "QuestTemplate",
    "ShopOffer",
    "Subscription",
    "Transfer",
    "Wallet",
    "WalletLedger",
    "CharacterTitle",
    "EnemyAbility",
    "EnemyTemplate",
    "EventTable",
    "EventTableEntry",
    "EventTemplate",
    "Expedition",
    "ExpeditionEvent",
    "Faction",
    "Inventory",
    "InventoryItem",
    "ItemTemplate",
    "LootEntry",
    "LootTable",
    "ModerationName",
    "ModuleInstance",
    "ModuleTemplate",
    "RecipeTemplate",
    "Reputation",
    "Ship",
    "ShipTemplate",
]
