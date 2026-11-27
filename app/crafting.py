"""Use cases этапа 6: крафт (рецепты, таймеры, отмена) и склад.

ТЗ-критерии:
- крафт только в космопорту; таймеры идут в реальном времени, даже офлайн;
- параллельные заказы в пределах числа производственных слотов;
- запуск только при полных материалах (иначе — список нехватки, ничего не списывается);
- отмена: частичный возврат материалов (% из рецепта), время не компенсируется;
- результат, не поместившийся в инвентарь, уходит в «Ожидает получения» и не теряется;
- завершения объединяются в уведомления; важные результаты — отдельно;
- склад в космопорту: недоступен в экспедиции; при освобождении места
  «Ожидает получения» автоматически возвращается в инвентарь.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, stores
from app.models import Timer
from app.models_game import Character, CraftJob, ItemTemplate, RecipeTemplate
from core.errors import DomainError


async def _require_port(session: AsyncSession, character: Character) -> None:
    from app.expeditions import get_ship

    ship = await get_ship(session, character.id)
    if ship.status != "dock":
        raise DomainError(
            "not_in_port",
            "Это доступно только в космопорту.",
            details={"status": ship.status},
        )


async def list_recipes(session: AsyncSession, character: Character) -> list[dict[str, Any]]:
    """Рецепты с пометкой доступности, именами предметов и нехваткой материалов."""
    from app.models_game import ItemTemplate

    recipes = (
        await session.scalars(
            sa.select(RecipeTemplate).where(RecipeTemplate.status == "published")
        )
    ).all()
    _, rows = await characters.list_inventory(session, character.id)
    owned = {r.code: r.qty for r in rows}
    # имена предметов одним запросом — коды игроку не показываем
    codes = {r.result_item_code for r in recipes} | {
        str(mat["code"]) for r in recipes for mat in r.materials
    }
    names: dict[str, str] = {}
    categories: dict[str, str] = {}
    for code, name, category in (
        await session.execute(
            sa.select(ItemTemplate.code, ItemTemplate.name, ItemTemplate.category).where(
                ItemTemplate.code.in_(codes)
            )
        )
    ).all():
        names[code] = name
        categories[code] = category
    result = []
    for recipe in recipes:
        materials = []
        missing = []
        for mat in recipe.materials:
            code = str(mat["code"])
            need = int(mat["qty"])
            have = owned.get(code, 0)
            materials.append(
                {"code": code, "name": names.get(code, code), "need": need, "have": have}
            )
            if have < need:
                missing.append({"code": code, "name": names.get(code, code), "lack": need - have})
        result.append(
            {
                "code": recipe.code,
                "name": recipe.name,
                "result": {
                    "code": recipe.result_item_code,
                    "name": names.get(recipe.result_item_code, recipe.result_item_code),
                    "category": categories.get(recipe.result_item_code, "special"),
                    "qty": recipe.result_qty,
                },
                "materials": materials,
                "duration_sec": recipe.duration_sec,
                "can_craft": not missing,
                "missing": missing,
            }
        )
    return result


async def running_jobs(session: AsyncSession, character: Character) -> list[CraftJob]:
    return list(
        (
            await session.scalars(
                sa.select(CraftJob)
                .where(CraftJob.character_id == character.id, CraftJob.status == "running")
                .order_by(CraftJob.started_at)
            )
        ).all()
    )


# расходники крафтятся мгновенно; модули/запчасти — за время (ТЗ заказчика)
INSTANT_CATEGORIES = {"consumable"}


def max_craftable(materials: list[dict[str, Any]], owned: dict[str, int]) -> int:
    """Сколько изделий хватит материалов (0 — не хватает даже на одно)."""
    limits = [
        owned.get(str(mat["code"]), 0)
        // max(1, int(mat.get("qty", mat.get("need", 1))))
        for mat in materials
    ]
    return min(limits) if limits else 0


async def result_category(session: AsyncSession, recipe: RecipeTemplate) -> str:
    from app.models_game import ItemTemplate

    return str(
        await session.scalar(
            sa.select(ItemTemplate.category).where(
                ItemTemplate.code == recipe.result_item_code
            )
        )
        or "special"
    )


async def start_craft(
    session: AsyncSession, character: Character, recipe_code: str, qty: int = 1
) -> CraftJob | None:
    """Запуск крафта ×qty. Расходники — мгновенно (None), остальное — заказ (job)."""
    await _require_port(session, character)
    if qty < 1:
        raise DomainError("bad_qty", "Количество: не меньше 1.")
    recipe = await session.scalar(
        sa.select(RecipeTemplate).where(
            RecipeTemplate.code == recipe_code, RecipeTemplate.status == "published"
        )
    )
    if recipe is None:
        raise DomainError("unknown_recipe", f"Неизвестный рецепт: {recipe_code}")

    # проверка материалов ДО списания (ТЗ: при нехватке ничего не списывается)
    _, rows = await characters.list_inventory(session, character.id)
    owned = {r.code: r.qty for r in rows}
    missing = []
    for mat in recipe.materials:
        lack = int(mat["qty"]) * qty - owned.get(str(mat["code"]), 0)
        if lack > 0:
            missing.append(f"{mat['code']} не хватает {lack}")
    if missing:
        raise DomainError("missing_materials", "Не хватает материалов: " + ", ".join(missing))

    instant = await result_category(session, recipe) in INSTANT_CATEGORIES
    if not instant:
        jobs = await running_jobs(session, character)
        if len(jobs) >= character.craft_slots:
            raise DomainError(
                "no_free_slot",
                f"Все производственные слоты заняты ({character.craft_slots}).",
            )

    for mat in recipe.materials:
        await characters.remove_items(
            session, character, code=str(mat["code"]), qty=int(mat["qty"]) * qty
        )

    if instant:
        # мгновенный крафт: результат сразу, таймер и слот не нужны
        await characters.grant_items(
            session, character, code=recipe.result_item_code,
            qty=recipe.result_qty * qty, allow_loss=True,
        )
        from app.quests import track

        for _ in range(qty):
            await track(
                session, character, "crafted", {"code": recipe.result_item_code}
            )
        await session.flush()
        return None

    now = datetime.now(UTC)
    job = CraftJob(
        character_id=character.id,
        recipe_id=recipe.id,
        qty=qty,
        slot_no=len(jobs) + 1,
        started_at=now,
        finishes_at=now + timedelta(seconds=recipe.duration_sec),
    )
    session.add(job)
    await session.flush()
    session.add(
        Timer(
            type="craft_done",
            character_id=character.id,
            payload={"job_id": str(job.id)},
            fires_at=job.finishes_at,
        )
    )
    await session.flush()
    return job


async def cancel_craft(session: AsyncSession, character: Character, job_index: int) -> str:
    """Отмена заказа (ТЗ): частичный возврат материалов, время не компенсируется."""
    await _require_port(session, character)
    jobs = await running_jobs(session, character)
    if not 1 <= job_index <= len(jobs):
        raise DomainError("no_job", f"Нет заказа №{job_index}.")
    job = jobs[job_index - 1]
    recipe = await session.get(RecipeTemplate, job.recipe_id)
    assert recipe is not None

    job.status = "cancelled"
    await stores.cancel_timer(
        session, timer_type="craft_done", payload_id_key="job_id", payload_id=str(job.id)
    )

    returned: list[str] = []
    pct = recipe.cancel_return_pct
    for mat in recipe.materials:
        qty = int(mat["qty"]) * pct // 100
        if qty > 0:
            given = await characters.grant_items(
                session, character, code=str(mat["code"]), qty=qty, allow_loss=True
            )
            returned.append(f"{given}× {mat['code']}")
    await session.flush()
    text = f"Заказ «{recipe.name}» отменён."
    if returned:
        text += " Возвращено материалов: " + ", ".join(returned) + "."
    return text


# --- завершение (worker) ---------------------------------------------------------


async def craft_done_handler(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Обработчик таймера craft_done.

    Идемпотентен (status != running → пропуск). Объединяет уведомления:
    завершает и другие просроченные заказы персонажа в одном сообщении;
    важные результаты (rare/epic/unique) сообщаются отдельно (ТЗ).
    """
    job = await session.get(CraftJob, uuid.UUID(str(payload["job_id"])))
    if job is None or job.status != "running":
        return
    now = datetime.now(UTC)

    due = list(
        (
            await session.scalars(
                sa.select(CraftJob)
                .where(
                    CraftJob.character_id == job.character_id,
                    CraftJob.status == "running",
                    CraftJob.finishes_at <= now,
                )
                .order_by(CraftJob.finishes_at)
            )
        ).all()
    )
    finished: list[str] = []
    important: list[str] = []
    for due_job in due:
        recipe = await session.get(RecipeTemplate, due_job.recipe_id)
        if recipe is None:
            continue
        due_job.status = "done"
        total_qty = recipe.result_qty * due_job.qty
        from app.quests import track

        for _ in range(due_job.qty):
            await track(
                session,
                await _character(session, job.character_id),
                "crafted",
                {"code": recipe.result_item_code},
            )
        # результат: в инвентарь; при нехватке места — в «Ожидает получения» (ТЗ, не теряется)
        try:
            await characters.grant_items(
                session,
                await _character(session, job.character_id),
                code=recipe.result_item_code,
                qty=total_qty,
                kind="ship",
                allow_loss=False,
            )
            line = f"{recipe.name} ×{total_qty}"
        except DomainError:
            await characters.grant_items(
                session,
                await _character(session, job.character_id),
                code=recipe.result_item_code,
                qty=total_qty,
                kind="waiting",
                allow_loss=True,
            )
            line = f"{recipe.name} ×{total_qty} (в разделе «Ожидает получения»)"
        result_template = await session.scalar(
            sa.select(ItemTemplate).where(ItemTemplate.code == recipe.result_item_code)
        )
        if result_template is not None and result_template.rarity in ("rare", "epic", "unique"):
            important.append(line)
        else:
            finished.append(line)

    if finished:
        await stores.add_outbox(
            session,
            character_id=job.character_id,
            text="Крафт завершён: " + "; ".join(finished) + ".",
        )
    for line in important:
        await stores.add_outbox(
            session, character_id=job.character_id, text=f"Важный результат крафта: {line}."
        )
    await session.flush()


async def _character(session: AsyncSession, character_id: uuid.UUID) -> Character:
    character = await session.get(Character, character_id)
    assert character is not None
    return character


# --- склад ------------------------------------------------------------------------


async def store_item(
    session: AsyncSession, character: Character, code: str, qty: int
) -> None:
    """Перемещение инвентарь корабля → склад (только в космопорту — ТЗ)."""
    await _require_port(session, character)
    if qty <= 0:
        raise DomainError("bad_qty", "Количество должно быть положительным.")
    await characters.get_inventory(session, character.id, "warehouse")  # склад существует
    removed = await characters.remove_items(session, character, code=code, qty=qty, kind="ship")
    if removed == 0:
        raise DomainError("no_item", f"Нет «{code}» в инвентаре.")
    try:
        await characters.grant_items(
            session, character, code=code, qty=removed, kind="warehouse", allow_loss=False
        )
    except DomainError:
        # склад полон — вернуть обратно в инвентарь
        await characters.grant_items(
            session, character, code=code, qty=removed, kind="ship", allow_loss=True
        )
        raise DomainError("warehouse_full", "Склад заполнен.") from None
    await session.flush()


async def take_item(
    session: AsyncSession, character: Character, code: str, qty: int
) -> None:
    """Склад → инвентарь корабля (только в космопорту — ТЗ)."""
    await _require_port(session, character)
    if qty <= 0:
        raise DomainError("bad_qty", "Количество должно быть положительным.")
    removed = await characters.remove_items(session, character, code=code, qty=qty, kind="warehouse")
    if removed == 0:
        raise DomainError("no_item", f"Нет «{code}» на складе.")
    try:
        await characters.grant_items(
            session, character, code=code, qty=removed, kind="ship", allow_loss=False
        )
    except DomainError:
        await characters.grant_items(
            session, character, code=code, qty=removed, kind="warehouse", allow_loss=True
        )
        raise DomainError("inventory_full", "В инвентаре нет места.") from None
    await session.flush()
