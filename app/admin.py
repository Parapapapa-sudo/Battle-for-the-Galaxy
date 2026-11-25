"""Сервисы админ-панели (этап 9): RBAC, аудит, модерация, конфигурация, аналитика.

ТЗ-критерии:
- роли: администратор (предметы, рецепты, враги, события, дроп, игроки, экономика,
  лимиты, аналитика), модератор (баны/разбан, применение существующих эффектов,
  просмотр профиля/истории), контент-менеджер (публикация только в своей зоне);
- все действия администраторов/модераторов — в append-only audit_log (ТЗ);
- балансировочные параметры редактируются без изменения кода и без перезапуска (ТЗ).
"""
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.models import AuditLog, ConfigParam
from app.models_game import (
    Account,
    AdminUser,
    Battle,
    Character,
    CharacterEffect,
    CharacterQuest,
    EffectTemplate,
    EventTemplate,
    Expedition,
    QuestTemplate,
    RecipeTemplate,
    WalletLedger,
)
from core.errors import DomainError

# Зоны ответственности контент-менеджера (ТЗ: публикует только в своей зоне)
CONTENT_MANAGER_TABLES = {
    "event_templates": EventTemplate,
    "quest_templates": QuestTemplate,
    "recipe_templates": RecipeTemplate,
}
ADMIN_ONLY_TABLES = {"config_params", "formulas", "item_templates", "enemy_templates"}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def authenticate(session: AsyncSession, api_key: str) -> AdminUser:
    admin = await session.scalar(
        sa.select(AdminUser).where(
            AdminUser.api_key_hash == hash_key(api_key), AdminUser.status == "active"
        )
    )
    if admin is None:
        raise DomainError("unauthorized", "Недействительный ключ администратора.")
    return admin


def require_role(admin: AdminUser, *roles: str) -> None:
    if admin.role not in roles:
        raise DomainError("forbidden", f"Требуется роль: {'/'.join(roles)}.")


async def audit(
    session: AsyncSession,
    admin: AdminUser,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    await stores.write_audit(
        session,
        actor_id=admin.login,
        actor_role=admin.role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
    )


async def create_admin(
    session: AsyncSession, admin: AdminUser, *, login: str, role: str
) -> str:
    """Создание администратора/модератора/контент-менеджера. Возвращает API-ключ (один раз)."""
    import secrets

    require_role(admin, "admin")
    if role not in ("admin", "moderator", "content_manager"):
        raise DomainError("bad_role", "Роль: admin|moderator|content_manager.")
    existing = await session.scalar(sa.select(AdminUser).where(AdminUser.login == login))
    if existing is not None:
        raise DomainError("admin_exists", f"«{login}» уже существует.")
    api_key = f"adm-{secrets.token_urlsafe(24)}"
    session.add(
        AdminUser(
            login=login,
            api_key_hash=hash_key(api_key),
            role=role,
            created_by=admin.login,
        )
    )
    await audit(session, admin, "create_admin", target_type="admin", target_id=login,
                after={"role": role})
    await session.flush()
    return api_key


# --- модерация ---------------------------------------------------------------------


async def ban_account(
    session: AsyncSession,
    admin: AdminUser,
    account_id: uuid.UUID,
    *,
    reason: str,
    hours: int | None = None,
) -> None:
    require_role(admin, "admin", "moderator")
    account = await session.get(Account, account_id)
    if account is None:
        raise DomainError("no_account", "Аккаунт не найден.")
    before = {"status": account.status, "ban_until": str(account.ban_until)}
    account.status = "banned"
    account.ban_reason = reason
    account.ban_until = (
        datetime.now(UTC) + timedelta(hours=hours) if hours else None
    )
    account.banned_by = admin.login
    await audit(
        session, admin, "ban_account", target_type="account", target_id=str(account_id),
        before=before, after={"status": "banned", "reason": reason},
    )
    await session.flush()


async def unban_account(session: AsyncSession, admin: AdminUser, account_id: uuid.UUID) -> None:
    require_role(admin, "admin", "moderator")
    account = await session.get(Account, account_id)
    if account is None:
        raise DomainError("no_account", "Аккаунт не найден.")
    account.status = "active"
    account.ban_until = None
    account.ban_reason = None
    await audit(
        session, admin, "unban_account", target_type="account", target_id=str(account_id)
    )
    await session.flush()


async def apply_effect(
    session: AsyncSession,
    admin: AdminUser,
    character_id: uuid.UUID,
    effect_code: str,
    params: dict[str, Any] | None = None,
    duration_sec: int | None = None,
) -> None:
    """Модератор применяет только существующий эффект (белый список — ТЗ)."""
    require_role(admin, "admin", "moderator")
    template = await session.scalar(
        sa.select(EffectTemplate).where(EffectTemplate.code == effect_code)
    )
    if template is None:
        raise DomainError("no_effect", f"Эффект «{effect_code}» не существует.")
    character = await session.get(Character, character_id)
    if character is None:
        raise DomainError("no_character", "Персонаж не найден.")
    duration = duration_sec if duration_sec is not None else template.duration_default_sec
    effect = CharacterEffect(
        character_id=character.id,
        effect_template_id=template.id,
        source_type="moderator",
        source_id=admin.login,
        params=params or {},
        expires_at=datetime.now(UTC) + timedelta(seconds=duration) if duration else None,
    )
    session.add(effect)
    await audit(
        session, admin, "apply_effect", target_type="character", target_id=str(character_id),
        after={"effect": effect_code, "params": params or {}},
    )
    await session.flush()


async def character_history(session: AsyncSession, character_id: uuid.UUID) -> dict[str, Any]:
    character = await session.get(Character, character_id)
    if character is None:
        raise DomainError("no_character", "Персонаж не найден.")
    bans = (
        await session.scalars(
            sa.select(AuditLog).where(
                AuditLog.target_type == "account", AuditLog.action.like("%ban%")
            )
        )
    ).all()
    effects = (
        await session.scalars(
            sa.select(CharacterEffect).where(CharacterEffect.character_id == character_id)
        )
    ).all()
    return {
        "character": {"id": str(character.id), "name": character.name, "status": character.status},
        "effects": [
            {"params": e.params, "applied_at": str(e.created_at), "expires_at": str(e.expires_at)}
            for e in effects
        ],
        "bans_related": len(bans),
    }


# --- конфигурация (админ) -------------------------------------------------------------


async def set_config(
    session: AsyncSession, admin: AdminUser, key: str, value: Any
) -> dict[str, Any]:
    require_role(admin, "admin")
    param = await session.get(ConfigParam, key)
    before = None if param is None else {"value": param.value}
    if param is None:
        param = ConfigParam(key=key, value=value, updated_by=admin.login)
        session.add(param)
    else:
        if not param.hot_reloadable:
            raise DomainError("not_hot_reloadable", f"«{key}» требует перезапуска.")
        param.value = value
        param.updated_by = admin.login
        param.version += 1
    await audit(
        session, admin, "set_config", target_type="config", target_id=key,
        before=before, after={"value": value},
    )
    await session.flush()
    return {"key": key, "value": value}


# --- контент: публикация (контент-менеджер — в своей зоне) ----------------------------


async def set_content_status(
    session: AsyncSession,
    admin: AdminUser,
    table: str,
    code: str,
    status: str,
) -> dict[str, Any]:
    if status not in ("draft", "published", "archived"):
        raise DomainError("bad_status", "Статус: draft|published|archived.")
    model = cast(Any, CONTENT_MANAGER_TABLES.get(table))
    if model is None:
        if table in ADMIN_ONLY_TABLES:
            require_role(admin, "admin")  # только админ
            raise DomainError("not_implemented", f"Редактор «{table}» — в разработке.")
        raise DomainError("unknown_table", f"Неизвестная таблица: {table}")
    require_role(admin, "admin", "content_manager")
    row = await session.scalar(sa.select(model).where(model.code == code))
    if row is None:
        raise DomainError("not_found", f"«{code}» не найден.")
    before = {"status": row.status}
    row.status = status
    await audit(
        session, admin, "set_content_status", target_type=table, target_id=code,
        before=before, after={"status": status},
    )
    await session.flush()
    return {"code": code, "status": status}


# --- аналитика (MVP) -------------------------------------------------------------------


async def whoami(admin: AdminUser) -> dict[str, Any]:
    """Информация о ключе — для UI (показ роли и доступных разделов)."""
    return {"login": admin.login, "role": admin.role}


async def lookup_character(
    session: AsyncSession, admin: AdminUser, name: str
) -> list[dict[str, Any]]:
    """Поиск персонажей по имени (для банов/эффектов в UI)."""
    require_role(admin, "admin", "moderator")
    rows = (
        await session.scalars(
            sa.select(Character).where(
                Character.name_normalized == name.strip().lower(),
                Character.status == "active",
            )
        )
    ).all()
    return [
        {
            "character_id": str(c.id),
            "account_id": str(c.account_id),
            "name": c.name,
        }
        for c in rows
    ]


async def list_effects(session: AsyncSession, admin: AdminUser) -> list[dict[str, Any]]:
    """Белый список эффектов (для формы наложения)."""
    require_role(admin, "admin", "moderator")
    templates = (
        await session.scalars(sa.select(EffectTemplate).order_by(EffectTemplate.code))
    ).all()
    return [{"code": t.code, "scope": t.scope} for t in templates]


async def analytics(session: AsyncSession, admin: AdminUser) -> dict[str, Any]:
    require_role(admin, "admin")
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    total_characters = int(
        await session.scalar(sa.select(sa.func.count()).select_from(Character)) or 0
    )
    expeditions_today = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Expedition).where(
                Expedition.created_at >= day_ago
            )
        )
        or 0
    )
    battles_today = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Battle).where(
                Battle.created_at >= day_ago
            )
        )
        or 0
    )
    market_volume = int(
        await session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(WalletLedger.delta), 0)).where(
                WalletLedger.reason.in_(["auction_sale", "eon_sell"]),
                WalletLedger.created_at >= day_ago,
            )
        )
        or 0
    )
    # воронка обучения: сколько персонажей завершили каждое задание
    funnel_rows = (
        await session.execute(
            sa.select(QuestTemplate.code, sa.func.count())
            .join(CharacterQuest, CharacterQuest.quest_id == QuestTemplate.id)
            .where(CharacterQuest.status == "completed")
            .group_by(QuestTemplate.code, QuestTemplate.chain_order)
            .order_by(QuestTemplate.chain_order)
        )
    ).all()
    return {
        "total_characters": total_characters,
        "expeditions_today": expeditions_today,
        "battles_today": battles_today,
        "market_volume_quant_today": market_volume,
        "tutorial_funnel": {code: count for code, count in funnel_rows},
    }
