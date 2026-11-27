"""Use cases этапа 5: модули (способности/навыки) и развитие.

ТЗ-критерии:
- способности активные, навыки пассивные; полученный модуль сразу доступен к установке;
- снятый модуль возвращается в инвентарь без потери прогресса;
- допустимое число одинаковых модулей — настройка модуля (max_per_ship);
- распределение опыта — только в космопорту; общий опыт не теряется при сбросе;
- сброс сборки — за спец. предмет/ресурс, формула повторных сбросов из config;
- характеристики корабля пересчитываются: база + модули (+ эффекты — позже).
"""
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.models_game import Character, ModuleInstance, ModuleTemplate, ShipTemplate
from core.errors import DomainError
from core.progression import effect_value, level_for_xp, reset_cost


async def _thresholds(session: AsyncSession) -> list[int]:
    return [int(v) for v in (await stores.get_param(session, "module.level_thresholds", [0, 50, 150]))]


def module_level(xp: int, thresholds: list[int]) -> int:
    return level_for_xp(xp, thresholds)


async def grant_module(
    session: AsyncSession, character: Character, module_code: str
) -> ModuleInstance:
    template = await session.scalar(
        sa.select(ModuleTemplate).where(ModuleTemplate.code == module_code)
    )
    if template is None:
        raise DomainError("unknown_module", f"Неизвестный модуль: {module_code}")
    instance = ModuleInstance(character_id=character.id, template_id=template.id)
    session.add(instance)
    await session.flush()
    return instance


async def _instances(
    session: AsyncSession, character_id: uuid.UUID
) -> list[tuple[ModuleInstance, ModuleTemplate]]:
    rows = (
        await session.execute(
            sa.select(ModuleInstance, ModuleTemplate)
            .join(ModuleTemplate, ModuleTemplate.id == ModuleInstance.template_id)
            .where(ModuleInstance.character_id == character_id)
            .order_by(ModuleInstance.created_at)
        )
    ).all()
    return [(i, t) for i, t in rows]


async def _require_dock(session: AsyncSession, character: Character) -> None:
    from app.expeditions import get_ship

    ship = await get_ship(session, character.id)
    if ship.status != "dock":
        raise DomainError(
            "not_in_port",
            "Модули и опыт можно менять только в космопорту.",
            details={"status": ship.status},
        )


async def list_modules(session: AsyncSession, character: Character) -> list[dict[str, Any]]:
    thresholds = await _thresholds(session)
    result = []
    for instance, template in await _instances(session, character.id):
        level = module_level(instance.xp_invested, thresholds)
        attack_bonus = _skill_attack_bonus(template, level)
        result.append(
            {
                "code": template.code,
                "name": template.name,
                "kind": template.kind,
                "level": level,
                "xp_invested": instance.xp_invested,
                "installed": instance.installed_slot is not None,
                "slot": instance.installed_slot,
                "attack_bonus": attack_bonus,
                "ability": (template.params or {}).get("ability"),
            }
        )
    return result


def _skill_attack_bonus(template: ModuleTemplate, level: int) -> int:
    bonus = (template.params or {}).get("attack_bonus")
    if not bonus:
        return 0
    return effect_value(int(bonus.get("base", 0)), int(bonus.get("per_level", 0)), level)


async def install_module(session: AsyncSession, character: Character, module_code: str) -> None:
    await _require_dock(session, character)
    rows = await _instances(session, character.id)
    target: tuple[ModuleInstance, ModuleTemplate] | None = next(
        ((i, t) for i, t in rows if t.code == module_code and i.installed_slot is None), None
    )
    if target is None:
        raise DomainError("no_module", f"Нет свободного модуля «{module_code}» в инвентаре.")
    instance, template = target
    installed_same = sum(1 for i, t in rows if t.code == module_code and i.installed_slot is not None)
    if installed_same >= template.max_per_ship:
        raise DomainError(
            "max_per_ship", f"Лимит модулей «{module_code}» на корабле: {template.max_per_ship}."
        )
    used_slots = [i.installed_slot for i, _ in rows if i.installed_slot is not None]
    instance.installed_slot = (max(used_slots) + 1) if used_slots else 1
    await session.flush()


async def uninstall_module(session: AsyncSession, character: Character, module_code: str) -> None:
    # снятие модулей — только в Мастерской (ТЗ заказчика)
    from app.crafting import _require_workshop

    await _require_workshop(session, character)

    await _require_dock(session, character)
    rows = await _instances(session, character.id)
    target = next(
        ((i, t) for i, t in rows if t.code == module_code and i.installed_slot is not None), None
    )
    if target is None:
        raise DomainError("no_module", f"Модуль «{module_code}» не установлен.")
    target[0].installed_slot = None  # прогресс сохраняется (ТЗ)
    await session.flush()


async def train_module(
    session: AsyncSession, character: Character, module_code: str, xp: int
) -> None:
    """Распределение опыта из общего пула на модуль — только в космопорту (ТЗ)."""
    await _require_dock(session, character)
    if xp <= 0:
        raise DomainError("bad_xp", "Опыт должен быть положительным.")
    if character.xp_pool < xp:
        raise DomainError("not_enough_xp", f"Недостаточно опыта: есть {character.xp_pool}.")
    rows = await _instances(session, character.id)
    target = next((i for i, t in rows if t.code == module_code), None)
    if target is None:
        raise DomainError("no_module", f"Модуля «{module_code}» у вас нет.")
    character.xp_pool -= xp
    target.xp_invested += xp
    await session.flush()


async def reset_build(session: AsyncSession, character: Character) -> int:
    """Полный сброс распределения опыта (ТЗ): опыт возвращается в пул (не теряется),
    оплата спец. ресурсом по формуле повторных сбросов из config."""
    await _require_dock(session, character)
    from app.characters import remove_items

    item = str(await stores.get_param(session, "progression.reset_item", "trinium_ore"))
    base_qty = int(await stores.get_param(session, "progression.reset_base_qty", 5))
    formula = str(await stores.get_param(session, "progression.reset_formula", "linear"))
    cost = reset_cost(base_qty, character.build_resets, formula=formula)

    removed = await remove_items(session, character, code=item, qty=cost)
    if removed < cost:
        raise DomainError(
            "not_enough_resources", f"Сброс сборки требует {cost}× {item}."
        )
    returned = 0
    for instance, _ in await _instances(session, character.id):
        returned += instance.xp_invested
        instance.xp_invested = 0
    character.xp_pool += returned
    character.build_resets += 1
    await session.flush()
    return returned


async def effective_attack(session: AsyncSession, character: Character) -> int:
    """Пересчёт характеристик: база корабля + бонусы установленных навыков (ТЗ)."""
    from app.expeditions import get_ship

    ship = await get_ship(session, character.id)
    template = await session.get(ShipTemplate, ship.template_id)
    assert template is not None
    base = int((template.base_stats or {}).get("attack", 5))
    thresholds = await _thresholds(session)
    bonus = 0
    for instance, mod_template in await _instances(session, character.id):
        if instance.installed_slot is None:
            continue
        bonus += _skill_attack_bonus(mod_template, module_level(instance.xp_invested, thresholds))
    return base + bonus


async def installed_abilities(session: AsyncSession, character: Character) -> list[dict[str, Any]]:
    """Активные способности установленных модулей (для боевых команд)."""
    abilities = []
    for instance, template in await _instances(session, character.id):
        if instance.installed_slot is None or template.kind != "ability":
            continue
        ability = dict((template.params or {}).get("ability") or {})
        if ability:
            ability["module_code"] = template.code
            abilities.append(ability)
    return abilities


async def ability_for_cmd(
    session: AsyncSession, character: Character, combat_cmd: str
) -> dict[str, Any] | None:
    for ability in await installed_abilities(session, character):
        if ability.get("combat_cmd") == combat_cmd:
            return ability
    return None
