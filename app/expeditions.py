"""Use cases этапа 3: космопорт, топливо, экспедиции, события, возвращение.

ТЗ-критерии:
- экспедиция бесконечна по структуре, ограничена топливом; топливо тратится на события;
- три сценария возвращения: voluntary (добровольное, без штрафа, топливо сохраняется),
  fuel_empty (автовозврат + поломки), defeat (потеря доли добычи, поломки);
- офлайн экспедиция не тикает — прогресс только по командам игрока;
- таймеры (прибытие) работают в реальном времени через таблицу timers + worker;
- уникальные события ≤1 за экспедицию; завершённое событие закрыто окончательно;
- игрок видит приблизительную опасность, точная скрыта.
"""
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import characters, stores
from app.locale import t
from app.models import Timer
from app.models_game import (
    Character,
    EventTableEntry,
    EventTemplate,
    Expedition,
    ExpeditionEvent,
    ItemTemplate,
    Ship,
)
from core.errors import DomainError
from core.expeditions import QueueEntry, danger_label, pick_variant, roll_queue
from core.hull import max_hull

# --- загрузка -----------------------------------------------------------------


async def get_ongoing_expedition(
    session: AsyncSession, character_id: uuid.UUID
) -> Expedition | None:
    return await session.scalar(
        sa.select(Expedition).where(
            Expedition.character_id == character_id,
            Expedition.status.in_(["active", "returning"]),
        )
    )


async def get_ship(session: AsyncSession, character_id: uuid.UUID) -> Ship:
    ship = await session.get(Ship, character_id)
    if ship is None:
        raise DomainError("no_ship", "Корабль не найден.")
    return ship


async def _table_entries(session: AsyncSession) -> list[QueueEntry]:
    rows = (
        await session.execute(
            sa.select(EventTemplate.code, EventTemplate.is_unique_per_expedition, EventTableEntry.weight)
            .join(EventTableEntry, EventTableEntry.event_template_id == EventTemplate.id)
            .where(EventTemplate.status == "published")
            .order_by(EventTemplate.code)
        )
    ).all()
    return [
        QueueEntry(code=r.code, weight=r.weight, is_unique=r.is_unique_per_expedition) for r in rows
    ]


# --- отправка -----------------------------------------------------------------


async def launch_expedition(session: AsyncSession, character: Character) -> Expedition:
    if await get_ongoing_expedition(session, character.id) is not None:
        raise DomainError("expedition_ongoing", "Экспедиция уже идёт.")
    ship = await get_ship(session, character.id)
    if ship.status != "dock":
        raise DomainError(
            "ship_not_in_port", "Корабль не в космопорту.", details={"status": ship.status}
        )
    if ship.fuel <= 0:
        raise DomainError("no_fuel", "Недостаточно топлива для старта.")

    queue_length = int(await stores.get_param(session, "expedition.queue_length", 10))
    entries = await _table_entries(session)
    if not entries:
        raise DomainError("content_error", "Таблицы событий пусты (контент не засеян).")

    expedition = Expedition(
        character_id=character.id,
        status="active",
        fuel_at_start=ship.fuel,
        fuel_remaining=ship.fuel,
        event_queue=roll_queue(entries, queue_length),
    )
    session.add(expedition)
    ship.status = "expedition"
    from app.quests import track

    await track(session, character, "expedition_start")
    await session.flush()
    return expedition


# --- ход: следующее событие ----------------------------------------------------


async def _current_offered_event(
    session: AsyncSession, expedition: Expedition
) -> ExpeditionEvent | None:
    return await session.scalar(
        sa.select(ExpeditionEvent)
        .where(ExpeditionEvent.expedition_id == expedition.id, ExpeditionEvent.status == "offered")
        .order_by(ExpeditionEvent.seq.desc())
        .limit(1)
    )


async def _extend_queue_if_needed(
    session: AsyncSession, expedition: Expedition, rng: random.Random
) -> None:
    """Экспедиции бесконечны по структуре (ТЗ): очередь пополняется при исчерпании.

    Уникальные события, уже попавшие в очередь, при продлении исключаются (ТЗ: ≤1 за экспедицию).
    """
    if expedition.queue_pos < len(expedition.event_queue):
        return
    queue_length = int(await stores.get_param(session, "expedition.queue_length", 10))
    entries = await _table_entries(session)
    used_uniques = {
        code
        for code in expedition.event_queue
        if any(e.is_unique and e.code == code for e in entries)
    }
    expedition.event_queue = list(expedition.event_queue) + roll_queue(
        entries, queue_length, rng, used_unique=used_uniques
    )


async def _snapshot_template(template: EventTemplate) -> dict[str, Any]:
    return {
        "code": template.code,
        "name": template.name,
        "description": template.description,
        "kind": template.kind,
        "danger_min": template.danger_min,
        "danger_max": template.danger_max,
        "can_refuse": template.can_refuse,
        "script": template.script,
    }


async def proceed(
    session: AsyncSession, character: Character, *, rng: random.Random | None = None
) -> tuple[ExpeditionEvent | None, str]:
    """Команда «Исследовать»: отправление в путь. Событие НЕ появляется сразу —
    оно прибывает через задержку путешествия (таймер exp_event):
    10с + (базовое топливо − текущее)/2. Пока идёт путешествие доступна только
    кнопка «Инфо»."""
    rng = rng or random.Random()
    from app.battles import get_active_battle

    if await get_active_battle(session, character.id) is not None:
        raise DomainError("battle_active", "Сначала завершите бой (attack / retreat).")
    expedition = await get_ongoing_expedition(session, character.id)
    _raise_if_repairing(expedition)
    _ship = await get_ship(session, character.id)
    if _ship.current_hull <= 1:
        raise DomainError("hull_too_low", t("hull_too_low", character.lang))
    if expedition is None or expedition.status != "active":
        raise DomainError("no_expedition", "Экспедиция не активна.")

    offered = await _current_offered_event(session, expedition)
    if offered is not None:
        snap = offered.snapshot
        raise DomainError(
            "event_pending",
            f"Сначала завершите событие «{snap.get('name', '?')}» (choice <n)"
            + (", decline — отказаться" if snap.get("can_refuse") else "")
            + ").",
        )

    now = datetime.now(UTC)
    if expedition.next_event_at is not None and expedition.next_event_at > now:
        return None, await describe_travel(session, character, expedition)

    spent = expedition.fuel_at_start - expedition.fuel_remaining
    base = int(await stores.get_param(session, "expedition.event_cooldown_base_sec", 10))
    delay = base + spent // 2  # 10с + (базовое топливо − текущее)/2
    expedition.next_event_at = now + timedelta(seconds=delay)
    session.add(
        Timer(
            type="exp_event",
            character_id=character.id,
            payload={"expedition_id": str(expedition.id)},
            fires_at=expedition.next_event_at,
        )
    )
    await session.flush()
    return None, await describe_travel(session, character, expedition)


async def arrive_event(
    session: AsyncSession, payload: dict[str, Any], *, rng: random.Random | None = None
) -> None:
    """Таймер exp_event: прибытие следующего события путешествия.

    Проверка топлива здесь: при нехватке — автовозврат (ТЗ) с уведомлением.
    Интерактивное событие ждёт выбора на кнопках; автоматическое разрешается сразу.
    """
    rng = rng or random.Random()
    expedition = await session.get(Expedition, uuid.UUID(str(payload["expedition_id"])))
    if expedition is None or expedition.status != "active":
        return
    if expedition.next_event_at is not None and expedition.next_event_at > datetime.now(UTC):
        return  # ещё в пути (защита от гонок)
    character = await session.get(Character, expedition.character_id)
    assert character is not None
    lang = character.lang

    await _extend_queue_if_needed(session, expedition, rng)
    code = expedition.event_queue[expedition.queue_pos]
    template = await session.scalar(sa.select(EventTemplate).where(EventTemplate.code == code))
    if template is None:
        raise DomainError("content_error", f"Шаблон события не найден: {code}")

    if expedition.fuel_remaining < template.fuel_cost:
        await _start_return(session, character, expedition, reason="fuel_empty")
        malfunctions = int(await stores.get_param(session, "fuel_empty.malfunctions", 1))
        await stores.add_outbox(
            session,
            character_id=character.id,
            text=t("ret_fuel_empty_note", lang, mal=malfunctions),
        )
        await session.flush()
        return

    expedition.fuel_remaining -= template.fuel_cost
    expedition.depth += 1
    expedition.next_event_at = None
    event = ExpeditionEvent(
        expedition_id=expedition.id,
        event_template_id=template.id,
        seq=expedition.queue_pos,
        snapshot=await _snapshot_template(template),
    )
    expedition.queue_pos += 1
    session.add(event)
    await session.flush()

    if template.kind == "automatic":
        variant = pick_variant(template.script.get("variants", []), rng)
        await _apply_outcome(session, character, expedition, event, variant, rng)
        await stores.add_outbox(
            session,
            character_id=character.id,
            text=describe_event(event, lang=lang),
        )
        await _queue_status(session, character)  # состояние — после исхода события
    else:
        await stores.add_outbox(
            session,
            character_id=character.id,
            text=describe_event(event, lang=lang),
        )
    await session.flush()


# --- разрешение событий --------------------------------------------------------


async def _apply_outcome(
    session: AsyncSession,
    character: Character,
    expedition: Expedition,
    event: ExpeditionEvent,
    outcome: dict[str, Any],
    rng: random.Random,
) -> list[str]:
    """Применяет исход: награды (прямой дроп при полном инвентаре — ТЗ), урон корпусу."""
    effects: list[str] = []
    battle_spec = outcome.get("battle")
    if battle_spec:
        # исход события — начало боя (ТЗ: события могут запускать пошаговые бои)
        from app.battles import start_battle

        battle = await start_battle(
            session,
            character,
            enemy_code=str(battle_spec["enemy"]),
            expedition=expedition if expedition.status == "active" else None,
        )
        from app.battles import describe_battle as _battle_intro

        effects.append(await _battle_intro(session, battle, lang=character.lang))
        event.status = "resolved"
        event.resolution = {
            "outcome": outcome,
            "effects": effects,
            "battle_id": str(battle.id),
        }
        event.resolved_at = datetime.now(UTC)
        from app.quests import track

        await track(session, character, "event_resolved", {"code": event.snapshot.get("code")})
        await session.flush()
        return effects

    for grant in outcome.get("grants", []):
        code, qty = str(grant["code"]), int(grant["qty"])
        given = await characters.grant_items(
            session, character, code=code, qty=qty, allow_loss=True
        )
        if given > 0:
            effects.append(t("effect_got", character.lang, qty=given, code=code))
        if given < qty:
            effects.append(t("effect_lost_full", character.lang, qty=qty - given, code=code))

    damage = int(outcome.get("hull_damage", 0))
    if damage > 0:
        ship = await get_ship(session, character.id)
        ship.current_hull = max(0, ship.current_hull - damage)
        effects.append(t("effect_hull_damage", character.lang, damage=damage))
        if ship.current_hull == 0:
            await _apply_defeat(session, character, expedition, rng)
            effects.append(t("effect_ship_down", character.lang))

    effects.extend(await _roll_expedition_drop(session, character, rng))

    event.status = "resolved"
    event.resolution = {"outcome": outcome, "effects": effects}
    event.resolved_at = datetime.now(UTC)
    from app.quests import track

    await track(session, character, "event_resolved", {"code": event.snapshot.get("code")})
    await session.flush()
    return effects


async def _roll_expedition_drop(
    session: AsyncSession, character: Character, rng: random.Random
) -> list[str]:
    """Бонусный дроп предметов с drop_weight > 0 (настраивается в админ-панели).

    Каждый предмет бросает независимый шанс drop_weight %; количество — из
    диапазона [drop_qty_min, drop_qty_max]. Полный инвентарь — прямой дроп с
    потерей остатка (правило ТЗ). Боевые события сюда не попадают: у врагов
    свои таблицы добычи.
    """
    templates = (
        await session.scalars(
            sa.select(ItemTemplate).where(ItemTemplate.drop_weight > 0).order_by(ItemTemplate.code)
        )
    ).all()
    lines: list[str] = []
    for template in templates:
        if rng.random() * 100 >= template.drop_weight:
            continue
        qty = rng.randint(template.drop_qty_min, max(template.drop_qty_min, template.drop_qty_max))
        given = await characters.grant_items(
            session, character, code=template.code, qty=qty, allow_loss=True
        )
        if given > 0:
            lines.append(t("effect_got", character.lang, qty=given, code=template.code))
        if given < qty:
            lines.append(t("effect_lost_full", character.lang, qty=qty - given, code=template.code))
    return lines


async def _apply_defeat(
    session: AsyncSession,
    character: Character,
    expedition: Expedition,
    rng: random.Random,
) -> None:
    """Поражение (ТЗ): автовозврат, поломки, потеря случайной доли добычи экспедиции.

    Ранее имевшиеся ресурсы не затрагиваются; теряются только предметы с
    флагом losable_on_defeat; редкие/уникальные по умолчанию защищены.
    """
    ship = await get_ship(session, character.id)
    malfunctions = int(await stores.get_param(session, "defeat.malfunctions", 1))
    ship.malfunctions += malfunctions

    loss_min = int(await stores.get_param(session, "defeat.loss_min_pct", 10))
    loss_max = int(await stores.get_param(session, "defeat.loss_max_pct", 30))
    loss_pct = rng.randint(loss_min, loss_max)

    # добыча текущей экспедиции = суммы grants в resolution её событий
    gained: dict[str, int] = {}
    resolved_events = (
        await session.scalars(
            sa.select(ExpeditionEvent).where(
                ExpeditionEvent.expedition_id == expedition.id,
                ExpeditionEvent.status == "resolved",
            )
        )
    ).all()
    for ev in resolved_events:
        outcome = (ev.resolution or {}).get("outcome", {})
        for grant in outcome.get("grants", []):
            gained[str(grant["code"])] = gained.get(str(grant["code"]), 0) + int(grant["qty"])

    lost_lines: list[str] = []
    for code, qty in gained.items():
        template = await session.scalar(sa.select(ItemTemplate).where(ItemTemplate.code == code))
        if template is None or not template.losable_on_defeat:
            continue  # редкие/уникальные защищены (ТЗ, флаг на предмете)
        lost = round(qty * loss_pct / 100)
        if lost > 0:
            removed = await characters.remove_items(session, character, code=code, qty=lost)
            if removed > 0:
                lost_lines.append(f"потеряно {removed}× {code}")

    await _start_return(session, character, expedition, reason="defeat", extra=lost_lines)


async def resolve_choice(
    session: AsyncSession,
    character: Character,
    key: str,
    *,
    rng: random.Random | None = None,
) -> ExpeditionEvent:
    rng = rng or random.Random()
    expedition = await get_ongoing_expedition(session, character.id)
    if expedition is None or expedition.status != "active":
        raise DomainError("no_expedition", "Экспедиция не активна.")
    event = await _current_offered_event(session, expedition)
    if event is None:
        raise DomainError("no_event", "Нет активного события — идите дальше (go).")
    snapshot = event.snapshot

    choices = snapshot.get("script", {}).get("choices", [])
    choice = next((c for c in choices if str(c.get("key")) == key), None)
    if choice is None:
        raise DomainError("bad_choice", f"Нет варианта «{key}».")
    outcome: dict[str, Any] = (
        pick_variant(choice["variants"], rng) if "variants" in choice else choice["outcome"]
    )
    await _apply_outcome(session, character, expedition, event, outcome, rng)
    await _queue_status(session, character)  # отдельное сообщение о состоянии
    return event


async def decline_event(session: AsyncSession, character: Character) -> ExpeditionEvent:
    expedition = await get_ongoing_expedition(session, character.id)
    if expedition is None or expedition.status != "active":
        raise DomainError("no_expedition", "Экспедиция не активна.")
    event = await _current_offered_event(session, expedition)
    if event is None:
        raise DomainError("no_event", "Нет активного события.")
    if not event.snapshot.get("can_refuse", False):
        raise DomainError("cannot_refuse", "Отказ от этого события невозможен.")
    event.status = "declined"
    event.resolved_at = datetime.now(UTC)
    event.resolution = {"declined": True}
    await _queue_status(session, character)  # отказ тоже завершает событие
    await session.flush()
    return event


# --- возвращение ---------------------------------------------------------------


async def _start_return(
    session: AsyncSession,
    character: Character,
    expedition: Expedition,
    *,
    reason: str,
    extra: list[str] | None = None,
) -> None:
    """Начало возвращения: задержка + таймер прибытия + сообщение игроку (ТЗ)."""
    delay = int(await stores.get_param(session, "expedition.return_delay_sec", 60))
    now = datetime.now(UTC)
    expedition.status = "returning"
    expedition.return_reason = reason
    expedition.return_eta = now + timedelta(seconds=delay)

    ship = await get_ship(session, character.id)
    ship.status = "returning"
    if reason == "fuel_empty":
        ship.malfunctions += int(await stores.get_param(session, "fuel_empty.malfunctions", 1))

    # перевыставление таймера (идемпотентно: старый отменяется)
    await stores.cancel_timer(
        session,
        timer_type="exp_return_arrival",
        payload_id_key="expedition_id",
        payload_id=str(expedition.id),
    )
    session.add(
        Timer(
            type="exp_return_arrival",
            character_id=character.id,
            payload={"expedition_id": str(expedition.id)},
            fires_at=expedition.return_eta,
        )
    )

    messages = {
        "voluntary": t(
            "ret_started", character.lang, delay=delay, fuel=expedition.fuel_remaining
        ),
        "fuel_empty": t("ret_fuel_empty", character.lang),
        "defeat": t("ret_defeat", character.lang),
    }
    text = messages[reason]
    if extra:
        text += "\n" + "\n".join(extra)
    # уведомление об отправлении идёт как ответ на команду (клавиатура обновится);
    # дубль в outbox не нужен — иначе игрок получает одно и то же дважды
    await session.flush()


async def request_return(session: AsyncSession, character: Character) -> None:
    """Добровольное возвращение (ТЗ): всегда успешно, без штрафа, топливо сохраняется,
    нельзя только во время активного события/боя."""
    from app.battles import get_active_battle

    if await get_active_battle(session, character.id) is not None:
        raise DomainError("battle_active", "Во время боя вернуться нельзя.")
    expedition = await get_ongoing_expedition(session, character.id)
    if expedition is None:
        raise DomainError("no_expedition", "Экспедиция не активна.")
    if expedition.status == "returning":
        raise DomainError("already_returning", "Корабль уже возвращается.")
    _raise_if_repairing(expedition)
    _raise_if_traveling(expedition)
    offered = await _current_offered_event(session, expedition)
    if offered is not None:
        snap = offered.snapshot
        hint = ", decline — отказаться" if snap.get("can_refuse") else ""
        raise DomainError(
            "event_pending", f"Нельзя вернуться во время события «{snap.get('name', '?')}» (choice <n>{hint})."
        )
    await _start_return(session, character, expedition, reason="voluntary")


async def arrive_expedition(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Обработчик таймера прибытия (worker). Идемпотентна: returning → completed."""
    expedition = await session.get(Expedition, uuid.UUID(str(payload["expedition_id"])))
    if expedition is None or expedition.status != "returning":
        return
    character = await session.get(Character, expedition.character_id)
    lang = character.lang if character is not None else "ru"
    expedition.status = "completed"
    expedition.arrived_at = datetime.now(UTC)
    ship = await get_ship(session, expedition.character_id)
    ship.status = "dock"
    if character is not None:
        character.hall = "main"  # после экспедиции игрок попадает в основной зал (ТЗ)
    await stores.add_outbox(
        session,
        character_id=expedition.character_id,
        text=t(
            "arrived",
            lang,
            depth=expedition.depth,
            fuel=expedition.fuel_remaining,
        ),
    )
    await session.flush()


async def describe_ship_status(session: AsyncSession, character: Character) -> str:
    """Сводка состояния: прочность (с индикатором), поломки, свободные слоты.

    Блоки разделены пустыми строками, у каждого — иконка (читаемость в чате).
    """
    from core.battle import hp_bar

    lang = character.lang
    ship = await get_ship(session, character.id)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    bar, pct = hp_bar(ship.current_hull, max(1, cap))
    inventory, rows = await characters.list_inventory(session, character.id)
    hull_block = t("status_hull", lang, cur=ship.current_hull, max=cap) + f"\n{bar} {pct}%"
    if inventory.size_limit is not None:
        free = max(0, inventory.size_limit - len(rows))
        inventory_line = t("status_inventory", lang, free=free, total=inventory.size_limit)
    else:
        inventory_line = t("status_inventory_unlimited", lang)
    return "\n\n".join(
        [
            t("status_title", lang),
            hull_block,
            t("status_malfunctions", lang, n=ship.malfunctions),
            inventory_line,
        ]
    )


async def _queue_status(session: AsyncSession, character: Character) -> None:
    """Отдельное сообщение о состоянии корабля — после КАЖДОГО завершения события."""
    await stores.add_outbox(
        session,
        character_id=character.id,
        text=await describe_ship_status(session, character),
    )


async def describe_travel(
    session: AsyncSession, character: Character, expedition: Expedition
) -> str:
    """Экран путешествия между событиями: текст + индикатор топлива (как HP)."""
    from core.battle import hp_bar

    lang = character.lang
    bar, pct = hp_bar(expedition.fuel_remaining, max(1, expedition.fuel_at_start))
    return "\n".join(
        [
            t("travel_started", lang),
            t("fuel_line", lang, cur=expedition.fuel_remaining, max=expedition.fuel_at_start),
            f"{bar} {pct}%",
        ]
    )


# --- «Залатать корабль» (ремонт в экспедиции) -------------------------------------


def _raise_if_traveling(expedition: Expedition | None) -> None:
    """В пути между событиями доступна только кнопка «Инфо»."""
    if expedition is not None and expedition.next_event_at is not None:
        now = datetime.now(UTC)
        if expedition.next_event_at > now:
            wait = int((expedition.next_event_at - now).total_seconds()) + 1
            raise DomainError(
                "traveling", f"Вы в пути — следующее событие через {wait}с.", details={"wait_sec": wait}
            )


def _raise_if_repairing(expedition: Expedition | None) -> None:
    """Ремонт идёт, пока patch_eta не снят обработчиком таймера (patch_complete).

    Строгая проверка (не только eta в будущем): иначе между истечением eta и
    срабатыванием таймера можно было запустить второй ремонт — двойной таймер,
    и уведомление о завершении приходило один раз на два ремонта.
    """
    if expedition is not None and expedition.patch_eta is not None:
        wait = max(1, int((expedition.patch_eta - datetime.now(UTC)).total_seconds()) + 1)
        raise DomainError(
            "ship_repairing", f"Идёт ремонт корабля — осталось {wait}с.", details={"wait_sec": wait}
        )


async def start_patch(session: AsyncSession, character: Character) -> int:
    """Начать ремонт: восстановит текущую прочность до максимума (с учётом травм).

    Длительность — config ship.patch_duration_sec (базово 20с), идёт в реальном
    времени (таймер, офлайн продолжается). Пока идёт ремонт: go/return недоступны.
    """
    from app.battles import get_active_battle

    if await get_active_battle(session, character.id) is not None:
        raise DomainError("battle_active", "Нельзя ремонтироваться в бою.")
    expedition = await get_ongoing_expedition(session, character.id)
    if expedition is None or expedition.status != "active":
        raise DomainError("no_expedition", "Экспедиция не активна.")
    _raise_if_repairing(expedition)
    _raise_if_traveling(expedition)
    offered = await _current_offered_event(session, expedition)
    if offered is not None:
        raise DomainError("event_pending", "Сначала завершите событие.")
    ship = await get_ship(session, character.id)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    if ship.current_hull >= cap:
        raise DomainError("hull_full", "Корабль в полном порядке, ремонт не нужен.")

    delay = int(await stores.get_param(session, "ship.patch_duration_sec", 20))
    expedition.patch_eta = datetime.now(UTC) + timedelta(seconds=delay)
    session.add(
        Timer(
            type="ship_patch",
            character_id=character.id,
            payload={"expedition_id": str(expedition.id)},
            fires_at=expedition.patch_eta,
        )
    )
    await session.flush()
    return delay


async def patch_complete(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Таймер ремонта: текущая прочность → максимум (base × (1 − 0.05×травмы))."""
    expedition = await session.get(Expedition, uuid.UUID(str(payload["expedition_id"])))
    if expedition is None or expedition.patch_eta is None:
        return
    character = await session.get(Character, expedition.character_id)
    lang = character.lang if character is not None else "ru"
    ship = await get_ship(session, expedition.character_id)
    cap = max_hull(ship.base_max_hull, ship.malfunctions)
    ship.current_hull = cap
    expedition.patch_eta = None
    await stores.add_outbox(
        session,
        character_id=expedition.character_id,
        text=t("patch_done", lang, hull=ship.current_hull, cap=cap),
    )
    await session.flush()


# --- статус --------------------------------------------------------------------


def describe_event(
    event: ExpeditionEvent, *, lang: str = "ru", low_max: int = 3, high_min: int = 7
) -> str:
    from app.locale import t

    snap = event.snapshot
    label = danger_label(
        int(snap.get("danger_min", 1)), int(snap.get("danger_max", 1)),
        low_max=low_max, high_min=high_min,
    )
    label = t(f"danger_{ {'низкая': 'low', 'средняя': 'medium', 'высокая': 'high'}.get(label, 'medium') }", lang)
    lines = [
        t("event_title", lang, name=snap.get("name", "?"), danger=label),
        snap.get("description", ""),
    ]
    if event.status == "resolved":
        outcome = (event.resolution or {}).get("outcome", {})
        lines.append(t("event_outcome", lang, message=outcome.get("message", "")))
        for effect in (event.resolution or {}).get("effects", []):
            lines.append(f"— {effect}")
    return "\n".join(line for line in lines if line)
