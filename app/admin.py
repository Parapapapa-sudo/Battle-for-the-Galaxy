"""Сервисы админ-панели (этап 9): RBAC, аудит, модерация, конфигурация, аналитика.

ТЗ-критерии:
- роли: администратор (предметы, рецепты, враги, события, дроп, игроки, экономика,
  лимиты, аналитика), модератор (баны/разбан, применение существующих эффектов,
  просмотр профиля/истории), контент-менеджер (публикация только в своей зоне);
- все действия администраторов/модераторов — в append-only audit_log (ТЗ);
- балансировочные параметры редактируются без изменения кода и без перезапуска (ТЗ).
"""
import hashlib
import time
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
    ItemTemplate,
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


# --- предметы (админ): справочник + редактор -----------------------------------------

ITEM_RARITIES = ("common", "rare", "epic", "unique")
DEFAULT_ITEM_CATEGORIES = ("resource", "consumable", "module", "repair_kit", "special")


def _item_to_dict(item: ItemTemplate) -> dict[str, Any]:
    return {
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "category": item.category,
        "rarity": item.rarity,
        "stack_limit": item.stack_limit,
        "is_unique": item.is_unique,
        "sellable": item.sellable,
        "tradable": item.tradable,
        "losable_on_defeat": item.losable_on_defeat,
        "drop_weight": item.drop_weight,
        "drop_qty_min": item.drop_qty_min,
        "drop_qty_max": item.drop_qty_max,
        "params": item.params or {},
    }


async def list_items(session: AsyncSession, admin: AdminUser) -> dict[str, Any]:
    """Все предметы + список типов (для выпадающих списков редактора)."""
    require_role(admin, "admin")
    items = (
        await session.scalars(sa.select(ItemTemplate).order_by(ItemTemplate.code))
    ).all()
    categories = sorted(
        set(DEFAULT_ITEM_CATEGORIES) | {i.category for i in items if i.category}
    )
    return {
        "items": [_item_to_dict(i) for i in items],
        "categories": categories,
        "rarities": list(ITEM_RARITIES),
    }


def _validate_item(data: dict[str, Any], *, create: bool) -> dict[str, Any]:
    """Проверка полей предмета; возвращает нормализованные значения."""
    import re

    errors: list[str] = []
    code = str(data.get("code", "")).strip().lower()
    if create and not re.fullmatch(r"[a-z0-9_]{2,64}", code):
        errors.append("код: 2–64 символа a-z, 0-9, _")
    name = str(data.get("name", "")).strip()
    if not name:
        errors.append("название обязательно")
    category = str(data.get("category", "")).strip()
    if not category:
        errors.append("тип предмета обязателен")
    rarity = str(data.get("rarity", "common")).strip()
    if rarity not in ITEM_RARITIES:
        errors.append(f"редкость: {'|'.join(ITEM_RARITIES)}")

    def _int(field: str, default: int, lo: int, hi: int) -> int:
        raw = data.get(field, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors.append(f"{field}: целое число")
            return default
        if not lo <= value <= hi:
            errors.append(f"{field}: от {lo} до {hi}")
        return value

    stack_limit = _int("stack_limit", 1, 1, 1_000_000)
    drop_weight = _int("drop_weight", 0, 0, 100)
    drop_qty_min = _int("drop_qty_min", 1, 1, 1_000_000)
    drop_qty_max = _int("drop_qty_max", 1, 1, 1_000_000)
    if drop_qty_max < drop_qty_min:
        errors.append("количество «до» меньше «от»")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        errors.append("параметры: JSON-объект")
        params = {}
    if errors:
        raise DomainError("bad_item", "Ошибка: " + "; ".join(errors))
    return {
        "code": code,
        "name": name,
        "description": str(data.get("description", "")).strip(),
        "category": category,
        "rarity": rarity,
        "stack_limit": stack_limit,
        "is_unique": bool(data.get("is_unique", False)),
        "sellable": bool(data.get("sellable", True)),
        "tradable": bool(data.get("tradable", True)),
        "losable_on_defeat": bool(data.get("losable_on_defeat", False)),
        "drop_weight": drop_weight,
        "drop_qty_min": drop_qty_min,
        "drop_qty_max": drop_qty_max,
        "params": params,
    }


async def create_item(
    session: AsyncSession, admin: AdminUser, data: dict[str, Any]
) -> dict[str, Any]:
    require_role(admin, "admin")
    fields = _validate_item(data, create=True)
    existing = await session.scalar(
        sa.select(ItemTemplate).where(ItemTemplate.code == fields["code"])
    )
    if existing is not None:
        raise DomainError("bad_item", f"Ошибка: предмет «{fields['code']}» уже существует.")
    item = ItemTemplate(**fields)
    session.add(item)
    await audit(
        session, admin, "create_item", target_type="item_templates",
        target_id=fields["code"], after=fields,
    )
    await session.flush()
    return _item_to_dict(item)


async def update_item(
    session: AsyncSession, admin: AdminUser, code: str, data: dict[str, Any]
) -> dict[str, Any]:
    require_role(admin, "admin")
    item = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == code))
    if item is None:
        raise DomainError("bad_item", f"Ошибка: предмет «{code}» не найден.")
    fields = _validate_item({**data, "code": code}, create=False)
    before = _item_to_dict(item)
    for key, value in fields.items():
        if key != "code":  # код — первичный идентификатор, не меняем
            setattr(item, key, value)
    await audit(
        session, admin, "update_item", target_type="item_templates",
        target_id=code, before=before, after=_item_to_dict(item),
    )
    await session.flush()
    return _item_to_dict(item)


# --- контент: редактор событий/заданий/рецептов (админ + контент-менеджер) -------------

CONTENT_KINDS: dict[str, Any] = {
    "event": EventTemplate,
    "quest": QuestTemplate,
    "recipe": RecipeTemplate,
}
_CONTENT_STATUSES = ("draft", "published", "archived")
_QUEST_KINDS = ("tutorial", "story")
_EVENT_KINDS = ("automatic", "interactive")


def _content_to_dict(kind: str, row: Any) -> dict[str, Any]:
    base = {"code": row.code, "name": row.name, "status": row.status}
    if kind == "event":
        return base | {
            "description": row.description,
            "kind": row.kind,
            "fuel_cost": row.fuel_cost,
            "danger_min": row.danger_min,
            "danger_max": row.danger_max,
            "can_refuse": row.can_refuse,
            "is_unique_per_expedition": row.is_unique_per_expedition,
            "script": row.script or {},
            "conditions": row.conditions or {},
        }
    if kind == "quest":
        return base | {
            "description": row.description,
            "kind": row.kind,
            "chain_order": row.chain_order,
            "objectives": row.objectives or [],
            "rewards": row.rewards or {},
        }
    return base | {
        "result_item_code": row.result_item_code,
        "result_qty": row.result_qty,
        "materials": row.materials or [],
        "duration_sec": row.duration_sec,
        "cancel_return_pct": row.cancel_return_pct,
        "requirements": row.requirements or {},
    }


async def list_content(session: AsyncSession, admin: AdminUser) -> dict[str, Any]:
    """Весь контент трёх типов — для редакторов админ-панели."""
    require_role(admin, "admin", "content_manager")
    result: dict[str, Any] = {}
    for kind, model in CONTENT_KINDS.items():
        rows = (await session.scalars(sa.select(model).order_by(model.code))).all()
        result[kind + "s"] = [_content_to_dict(kind, row) for row in rows]
    return result


def _validate_content(kind: str, data: dict[str, Any], *, create: bool) -> dict[str, Any]:
    import re

    errors: list[str] = []
    code = str(data.get("code", "")).strip().lower()
    if create and not re.fullmatch(r"[a-z0-9_]{2,64}", code):
        errors.append("код: 2–64 символа a-z, 0-9, _")
    name = str(data.get("name", "")).strip()
    if not name:
        errors.append("название обязательно")
    status = str(data.get("status", "published")).strip()
    if status not in _CONTENT_STATUSES:
        errors.append(f"статус: {'|'.join(_CONTENT_STATUSES)}")

    def _int(field: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(data.get(field, default))
        except (TypeError, ValueError):
            errors.append(f"{field}: целое число")
            return default
        if not lo <= value <= hi:
            errors.append(f"{field}: от {lo} до {hi}")
        return value

    def _json(field: str, default: Any, expected: type) -> Any:
        value = data.get(field, default)
        if not isinstance(value, expected):
            errors.append(f"{field}: {'список' if expected is list else 'объект'} JSON")
            return default
        return value

    fields: dict[str, Any] = {"code": code, "name": name, "status": status}
    if kind == "event":
        ekind = str(data.get("kind", "")).strip()
        if ekind not in _EVENT_KINDS:
            errors.append(f"тип события: {'|'.join(_EVENT_KINDS)}")
        danger_min = _int("danger_min", 1, 1, 10)
        danger_max = _int("danger_max", 1, 1, 10)
        if danger_max < danger_min:
            errors.append("опасность «до» меньше «от»")
        script = _json("script", {}, dict)
        if ekind == "interactive" and not script.get("choices"):
            errors.append("для interactive нужен script.choices (непустой список)")
        if ekind == "automatic" and not script.get("variants"):
            errors.append("для automatic нужен script.variants (непустой список)")
        fields |= {
            "description": str(data.get("description", "")).strip(),
            "kind": ekind,
            "fuel_cost": _int("fuel_cost", 1, 0, 1000),
            "danger_min": danger_min,
            "danger_max": danger_max,
            "can_refuse": bool(data.get("can_refuse", True)),
            "is_unique_per_expedition": bool(data.get("is_unique_per_expedition", False)),
            "script": script,
            "conditions": _json("conditions", {}, dict),
        }
    elif kind == "quest":
        qkind = str(data.get("kind", "tutorial")).strip()
        if qkind not in _QUEST_KINDS:
            errors.append(f"тип задания: {'|'.join(_QUEST_KINDS)}")
        objectives = _json("objectives", [], list)
        if not objectives:
            errors.append("нужна хотя бы одна цель (objectives)")
        fields |= {
            "description": str(data.get("description", "")).strip(),
            "kind": qkind,
            "chain_order": _int("chain_order", 0, 0, 1000),
            "objectives": objectives,
            "rewards": _json("rewards", {}, dict),
        }
    else:  # recipe
        result_code = str(data.get("result_item_code", "")).strip()
        if not result_code:
            errors.append("код предмета результата обязателен")
        materials = _json("materials", [], list)
        for mat in materials:
            if not isinstance(mat, dict) or not mat.get("code") or int(mat.get("qty", 0)) < 1:
                errors.append('материалы: список вида [{"code": "...", "qty": N}]')
                break
        fields |= {
            "result_item_code": result_code,
            "result_qty": _int("result_qty", 1, 1, 1000),
            "materials": materials,
            "duration_sec": _int("duration_sec", 60, 0, 604800),
            "cancel_return_pct": _int("cancel_return_pct", 50, 0, 100),
            "requirements": _json("requirements", {}, dict),
        }
    if errors:
        raise DomainError(
            "bad_content", "Ошибка при сохранении. Некорректные значения полей: " + "; ".join(errors)
        )
    return fields


async def _validate_recipe_result(session: AsyncSession, fields: dict[str, Any]) -> None:
    """Предмет результата рецепта должен существовать."""
    exists = await session.scalar(
        sa.select(ItemTemplate.id).where(ItemTemplate.code == fields["result_item_code"])
    )
    if exists is None:
        raise DomainError(
            "bad_content",
            "Ошибка при сохранении. Некорректные значения полей: "
            f"предмет результата «{fields['result_item_code']}» не существует",
        )


async def create_content(
    session: AsyncSession, admin: AdminUser, kind: str, data: dict[str, Any]
) -> dict[str, Any]:
    require_role(admin, "admin", "content_manager")
    model = CONTENT_KINDS.get(kind)
    if model is None:
        raise DomainError("bad_kind", f"Тип контента: {'|'.join(CONTENT_KINDS)}.")
    fields = _validate_content(kind, data, create=True)
    if kind == "recipe":
        await _validate_recipe_result(session, fields)
    if await session.scalar(sa.select(model).where(model.code == fields["code"])) is not None:
        raise DomainError(
            "bad_content",
            f"Ошибка при сохранении. Некорректные значения полей: «{fields['code']}» уже существует",
        )
    row = model(**fields)
    session.add(row)
    await audit(
        session, admin, "create_content", target_type=kind, target_id=fields["code"],
        after={"name": fields["name"]},
    )
    await session.flush()
    return _content_to_dict(kind, row)


async def update_content(
    session: AsyncSession, admin: AdminUser, kind: str, code: str, data: dict[str, Any]
) -> dict[str, Any]:
    require_role(admin, "admin", "content_manager")
    model = CONTENT_KINDS.get(kind)
    if model is None:
        raise DomainError("bad_kind", f"Тип контента: {'|'.join(CONTENT_KINDS)}.")
    row = await session.scalar(sa.select(model).where(model.code == code))
    if row is None:
        raise DomainError("not_found", f"Контент «{code}» не найден.")
    fields = _validate_content(kind, {**data, "code": code}, create=False)
    if kind == "recipe":
        await _validate_recipe_result(session, fields)
    before = _content_to_dict(kind, row)
    for key, value in fields.items():
        if key != "code":
            setattr(row, key, value)
    await audit(
        session, admin, "update_content", target_type=kind, target_id=code,
        before=before, after=_content_to_dict(kind, row),
    )
    await session.flush()
    return _content_to_dict(kind, row)


async def delete_content(
    session: AsyncSession, admin: AdminUser, kind: str, code: str
) -> dict[str, Any]:
    """Удаление: жёсткое, если контент нигде не используется; иначе — архив.

    События/задания обычно связаны с историей игроков (FK) — тогда status
    'archived': из игры контент исчезает, история сохраняется.
    """
    require_role(admin, "admin", "content_manager")
    model = CONTENT_KINDS.get(kind)
    if model is None:
        raise DomainError("bad_kind", f"Тип контента: {'|'.join(CONTENT_KINDS)}.")
    row = await session.scalar(sa.select(model).where(model.code == code))
    if row is None:
        raise DomainError("not_found", f"Контент «{code}» не найден.")
    before = _content_to_dict(kind, row)
    mode = "hard"
    if kind == "quest":
        # CharacterQuest → CASCADE: жёсткое удаление стёрло бы прогресс игроков
        used = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(CharacterQuest)
                .where(CharacterQuest.quest_id == row.id)
            )
            or 0
        )
        if used:
            mode = "archived"
    if mode == "hard":
        try:
            async with session.begin_nested():
                await session.delete(row)
                await session.flush()
        except sa.exc.IntegrityError:  # ExpeditionEvent → RESTRICT (история игроков)
            mode = "archived"
            row.status = "archived"
            await session.flush()
    else:
        row.status = "archived"
        await session.flush()
    await audit(
        session, admin, "delete_content", target_type=kind, target_id=code,
        before=before, after={"mode": mode},
    )
    return {"code": code, "mode": mode}


# --- аналитика (MVP) -------------------------------------------------------------------

# МСК без перехода на летнее время (UTC+3) — для журнала аудита в админ-панели
_MSK = timedelta(hours=3)
_STARTED_MONO = time.monotonic()  # аптайм процесса, поднявшего модуль

_AUDIT_ACTION_LABELS = {
    "set_config": "изменил конфиг",
    "ban_account": "забанил аккаунт",
    "unban_account": "разбанил аккаунт",
    "apply_effect": "наложил эффект",
    "set_content_status": "сменил статус контента",
    "create_admin": "создал администратора",
    "create_item": "создал предмет",
    "update_item": "изменил предмет",
    "create_content": "создал контент",
    "update_content": "изменил контент",
    "delete_content": "удалил контент",
    "health_check": "проверил работоспособность",
    "set_health_schedule": "настроил расписание проверок",
}


def _rss_mb() -> float | None:
    """Память процесса (RSS/рабочий набор), МБ. stdlib: psapi (Windows) / resource (POSIX)."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _PMC()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wt.HANDLE  # иначе хендл режется до int
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        psapi.GetProcessMemoryInfo.restype = wt.BOOL
        if psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return round(float(counters.WorkingSetSize) / 1024 / 1024, 1)
    except (AttributeError, OSError):
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined]
            return round(float(usage.ru_maxrss) / 1024, 1)
        except Exception:  # noqa: BLE001 — метрика опциональна
            return None
    return None


async def server_load(session: AsyncSession, admin: AdminUser) -> dict[str, Any]:
    """Нагрузка на сервер: CPU/память процесса API + состояние БД и очередей."""
    import asyncio
    import os

    require_role(admin, "admin")
    t0 = os.times()
    await asyncio.sleep(0.2)  # окно замера CPU
    t1 = os.times()
    cpu_pct = round(((t1.user - t0.user) + (t1.system - t0.system)) / 0.2 * 100)

    db_size = int(await session.scalar(sa.text("SELECT pg_database_size(current_database())")) or 0)
    db_conns = int(
        await session.scalar(
            sa.text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        )
        or 0
    )
    from app.models import Outbox, Timer

    timers_pending = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Timer).where(Timer.status == "pending")
        )
        or 0
    )
    outbox_pending = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Outbox).where(Outbox.status == "pending")
        )
        or 0
    )
    outbox_failed = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(Outbox).where(Outbox.status == "failed")
        )
        or 0
    )
    return {
        "cpu_percent": cpu_pct,
        "cpu_cores": os.cpu_count() or 1,
        "memory_mb": _rss_mb(),
        "uptime_sec": int(time.monotonic() - _STARTED_MONO),
        "db_size_mb": round(db_size / 1024 / 1024, 1),
        "db_connections": db_conns,
        "timers_pending": timers_pending,
        "outbox_pending": outbox_pending,
        "outbox_failed": outbox_failed,
    }


def _audit_summary(row: AuditLog) -> str:
    """Одна строка о сути изменения: действие + цель + краткая деталь.

    Подробности не нужны (ТЗ заказчика): для конфига — новое значение, для
    правок сущностей — список изменённых полей, для создания — имя объекта.
    """
    import json

    label = _AUDIT_ACTION_LABELS.get(row.action, row.action)
    target = f" {row.target_id}" if row.target_id else ""
    detail = ""
    if row.action == "set_config" and row.after is not None:
        value = json.dumps(row.after.get("value"), ensure_ascii=False, default=str)[:40]
        detail = f" → {value}"
    elif row.after is not None and row.before is not None:
        changed = [k for k, v in row.after.items() if row.before.get(k) != v]
        if changed:
            detail = " — поля: " + ", ".join(changed[:6])
    elif row.after is not None and row.after.get("name"):
        detail = f" — «{row.after['name']}»"
    elif row.after is not None and row.after.get("effect"):
        detail = f" — {row.after['effect']}"
    return f"{label}{target}{detail}"


async def audit_log_view(
    session: AsyncSession, admin: AdminUser, *, limit: int = 50
) -> dict[str, Any]:
    """Журнал аудита для админ-панели: время МСК, ключ, суть изменения."""
    require_role(admin, "admin")
    rows = (
        await session.scalars(sa.select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    ).all()
    return {
        "entries": [
            {
                "time_msk": (row.at + _MSK).strftime("%d.%m.%Y %H:%M:%S"),
                "actor": f"{row.actor_id} ({row.actor_role})",
                "summary": _audit_summary(row),
            }
            for row in rows
        ]
    }


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
            sa.select(QuestTemplate.code, QuestTemplate.name, sa.func.count())
            .join(CharacterQuest, CharacterQuest.quest_id == QuestTemplate.id)
            .where(CharacterQuest.status == "completed")
            .group_by(QuestTemplate.code, QuestTemplate.name, QuestTemplate.chain_order)
            .order_by(QuestTemplate.chain_order)
        )
    ).all()
    return {
        "total_characters": total_characters,
        "expeditions_today": expeditions_today,
        "battles_today": battles_today,
        "market_volume_quant_today": market_volume,
        "tutorial_funnel": [
            {"code": code, "name": name, "completed": count}
            for code, name, count in funnel_rows
        ],
    }
