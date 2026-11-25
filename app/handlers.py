"""Реестр игровых команд этапа 1 (общий для VK- и консольного адаптера).

Разделение скоупов (ТЗ: последовательность команд):
- account-команды (start/factions) — до создания персонажа, lock на аккаунт;
- character-команды (me/inv/ship) — после создания, lock на персонажа.
"""
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.quests as quests_mod
from app import battles, characters, crafting, economy, expeditions, market, modules, stores
from app.bus import CommandContext, CommandResult
from app.models_game import (
    Battle,
    Character,
    ExpeditionEvent,
    Faction,
    FactionEvent,
    FactionEventParticipant,
    RecipeTemplate,
    Reputation,
)
from core.errors import DomainError
from core.hull import max_hull

USAGE_NO_CHARACTER = (
    "У вас ещё нет персонажа. Создайте: start <имя> <код_фракции>. "
    "Список фракций: factions"
)


# --- account scope ------------------------------------------------------------

async def cmd_factions(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    factions = await characters.list_factions(session)
    if not factions:
        return CommandResult(message="Фракции пока не созданы (контент не засеян).")
    lines = [f"{f.code} — {f.name}" for f in factions]
    return CommandResult(
        message="Фракции:\n" + "\n".join(lines),
        data={"factions": [{"code": f.code, "name": f.name} for f in factions]},
    )


async def cmd_start(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    assert ctx.account_id is not None
    text = str(args.get("text", "")).strip()

    # Разбор: "Иван azimuth" (код фракции — последний токен, если он известен),
    # иначе вся строка — имя и просим выбрать фракцию.
    name, faction_code = text, None
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2 and await characters.get_faction_by_code(session, parts[1]):
        name, faction_code = parts[0], parts[1]

    if not name:
        factions = await characters.list_factions(session)
        codes = ", ".join(f.code for f in factions)
        return CommandResult(
            message=(
                "Создание персонажа: start <имя> <код_фракции>.\n"
                f"Доступные фракции: {codes or 'нет (контент не засеян)'}"
            )
        )
    if faction_code is None:
        factions = await characters.list_factions(session)
        lines = [f"{f.code} — {f.name}" for f in factions]
        return CommandResult(
            message=f"Выберите фракцию для «{name}»:\n" + "\n".join(lines),
            data={"need_faction": True, "name": name},
        )

    character = await characters.create_character(
        session, account_id=ctx.account_id, name=name, faction_code=faction_code
    )
    faction = await characters.get_faction_by_code(session, faction_code)
    return CommandResult(
        message=(
            f"Персонаж «{character.name}» создан. Фракция: {faction.name if faction else faction_code}.\n"
            "Обучение начинается с первой экспедиции (этап 3).\n"
            "Команды: me — профиль, inv — инвентарь, ship — корабль."
        ),
        data={"character_id": str(character.id)},
    )


# --- character scope ----------------------------------------------------------

async def _load(session: AsyncSession, ctx: CommandContext) -> Character:
    assert ctx.character_id is not None
    return await characters.load_character(session, ctx.character_id)


async def cmd_me(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    faction_row = await session.get(Faction, character.faction_id)
    rep = await session.scalar(
        sa.select(Reputation).where(
            Reputation.character_id == character.id, Reputation.is_active
        )
    )
    return CommandResult(
        message=(
            f"{character.name} — {faction_row.name if faction_row else '?'}\n"
            f"Репутация: {rep.value if rep else 0}\n"
            f"Опыт (пул): {character.xp_pool}"
        ),
        data={
            "name": character.name,
            "faction": faction_row.code if faction_row else None,
            "reputation": rep.value if rep else 0,
            "xp_pool": character.xp_pool,
        },
    )


async def cmd_inv(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    kind = str(args.get("kind", "ship"))
    if kind not in ("ship", "warehouse", "waiting"):
        raise DomainError("bad_kind", "Раздел: ship | warehouse | waiting")
    inventory, rows = await characters.list_inventory(session, character.id, kind)
    if not rows:
        return CommandResult(message="Инвентарь пуст.")
    limit = inventory.size_limit if inventory.size_limit is not None else "∞"
    lines = [f"[{r.qty}×] {r.name} ({r.code})" for r in rows]
    return CommandResult(
        message=f"Инвентарь ({kind}, {len(rows)}/{limit}):\n" + "\n".join(lines),
        data={"kind": kind, "items": [r.__dict__ for r in rows]},
    )


async def cmd_ship(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    ship, template = await characters.get_ship(session, character.id)
    return CommandResult(
        message=(
            f"{template.name}\n"
            f"Прочность: {ship.current_hull}/{max_hull(ship.base_max_hull, ship.malfunctions)} "
            f"(поломки: {ship.malfunctions})\n"
            f"Топливо: {ship.fuel}\nСтатус: {ship.status}"
        ),
        data={
            "hull": ship.current_hull,
            "max_hull": max_hull(ship.base_max_hull, ship.malfunctions),
            "malfunctions": ship.malfunctions,
            "fuel": ship.fuel,
            "status": ship.status,
        },
    )


# --- этап 3: экспедиции ---------------------------------------------------------


async def cmd_exp(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    if expedition is None:
        return CommandResult(message="Экспедиция не начата. Команда: launch")
    if expedition.status == "returning":
        return CommandResult(
            message=(
                f"Возвращение в космопорт ({expedition.return_reason}). "
                f"Глубина: {expedition.depth}. Осталось топлива: {expedition.fuel_remaining}."
            )
        )
    offered = await expeditions._current_offered_event(session, expedition)
    if offered is not None:
        return CommandResult(message=expeditions.describe_event(offered))
    low = int(await stores.get_param(session, "danger.low_max", 3))
    high = int(await stores.get_param(session, "danger.high_min", 7))
    last = await session.scalar(
        sa.select(ExpeditionEvent)
        .where(ExpeditionEvent.expedition_id == expedition.id)
        .order_by(ExpeditionEvent.seq.desc())
        .limit(1)
    )
    text = (
        f"Экспедиция активна. Глубина: {expedition.depth}. "
        f"Топливо: {expedition.fuel_remaining}/{expedition.fuel_at_start}.\n"
    )
    if last is not None and last.status == "resolved":
        text += expeditions.describe_event(last, low_max=low, high_min=high) + "\n"
    text += "Команда: go — идти дальше, return — вернуться в космопорт."
    return CommandResult(message=text)


async def cmd_launch(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    expedition = await expeditions.launch_expedition(session, character)
    return CommandResult(
        message=(
            f"Экспедиция началась! Топливо: {expedition.fuel_remaining}. "
            "Команда go — двигаться дальше."
        ),
        data={"expedition_id": str(expedition.id)},
    )


async def cmd_go(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    event, note = await expeditions.proceed(session, character)
    if event is None:
        return CommandResult(message=note)  # автовозврат по топливу (зафиксирован)
    low = int(await stores.get_param(session, "danger.low_max", 3))
    high = int(await stores.get_param(session, "danger.high_min", 7))
    text = expeditions.describe_event(event, low_max=low, high_min=high)
    if note:
        text = note + "\n" + text
    return CommandResult(message=text)


async def cmd_choice(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    key = str(args.get("key", "")).strip()
    if not key:
        raise DomainError("bad_choice", "Укажите вариант: choice <n>")
    event = await expeditions.resolve_choice(session, character, key)
    return CommandResult(message=expeditions.describe_event(event))


async def cmd_decline(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    short_id = str(args.get("id", "")).strip()
    if short_id:
        # отказ от входящей передачи (этап 7)
        await economy.reject_transfer(session, character, short_id)
        return CommandResult(message="Передача отклонена. Активы отправителю возвращены.")
    event = await expeditions.decline_event(session, character)
    return CommandResult(
        message=f"Отказ от события «{event.snapshot.get('name', '?')}». Команда go — идти дальше."
    )


async def cmd_return(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    await expeditions.request_return(session, character)
    return CommandResult(
        message="Возвращение началось. Сообщение о прибытии придёт автоматически."
    )


# --- этап 4: бой -----------------------------------------------------------------


async def _battle_message(
    session: AsyncSession, battle: Battle, *, header: bool = True
) -> str:
    lines: list[str] = []
    if header:
        lines.append(await battles.describe_battle(session, battle))
    turns = await battles.battle_journal(session, battle, limit=3)
    for turn in turns:
        lines.append(f"[{turn.actor}] {turn.summary}")
    return "\n".join(lines)


async def cmd_attack(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.player_attack(session, character)
    return CommandResult(message=await _battle_message(session, battle))


async def cmd_use(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_item", "Укажите предмет: use <код>")
    battle = await battles.player_use_item(session, character, code)
    return CommandResult(message=await _battle_message(session, battle))


async def cmd_retreat(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.player_retreat(session, character)
    return CommandResult(message=await _battle_message(session, battle))


async def cmd_battle(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.get_active_battle(session, character.id)
    if battle is None:
        return CommandResult(message="Нет активного боя.")
    return CommandResult(message=await _battle_message(session, battle))


async def cmd_battle_log(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.get_active_battle(session, character.id)
    if battle is None:
        return CommandResult(message="Нет активного боя.")
    turns = await battles.battle_journal(session, battle, limit=10)
    lines = [f"— ход {t.turn_no} ({t.actor}): {t.summary}" for t in turns]
    for t in turns:
        for entry in t.log:
            lines.append(f"    · {entry}")
    return CommandResult(message="Боевой журнал:\n" + "\n".join(lines))


# --- этап 5: модули и развитие ----------------------------------------------------


async def cmd_mods(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    mods = await modules.list_modules(session, character)
    if not mods:
        return CommandResult(message="У вас нет модулей.")
    lines = [f"Опыт (пул): {character.xp_pool}. Сбросов сборки: {character.build_resets}."]
    for m in mods:
        status = f"слот {m['slot']}" if m["installed"] else "в инвентаре"
        extra = ""
        if m["attack_bonus"]:
            extra = f", атака +{m['attack_bonus']}"
        if m["ability"]:
            extra += f", команда боя: {m['ability'].get('combat_cmd')}"
        lines.append(
            f"[{m['kind']}] {m['name']} ({m['code']}) — ур. {m['level']} "
            f"({m['xp_invested']} опыта), {status}{extra}"
        )
    lines.append("Команды: install <код> | uninstall <код> | train <код> <опыт> | resetbuild")
    return CommandResult(message="\n".join(lines))


async def cmd_install(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_module", "Укажите модуль: install <код>")
    await modules.install_module(session, character, code)
    return CommandResult(message=f"Модуль «{code}» установлен.")


async def cmd_uninstall(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_module", "Укажите модуль: uninstall <код>")
    await modules.uninstall_module(session, character, code)
    return CommandResult(message=f"Модуль «{code}» снят (прогресс сохранён).")


async def cmd_train(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    xp = int(args.get("xp", 0))
    if not code or xp <= 0:
        raise DomainError("bad_args", "Формат: train <код> <опыт>")
    await modules.train_module(session, character, code, xp)
    return CommandResult(
        message=f"Вложено {xp} опыта в «{code}». Остаток пула: {character.xp_pool}."
    )


async def cmd_resetbuild(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    returned = await modules.reset_build(session, character)
    return CommandResult(
        message=(
            f"Сборка сброшена: {returned} опыта возвращено в пул "
            f"(сейчас {character.xp_pool}). Модули остались установленными."
        )
    )


async def cmd_ability(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    cmd = str(args.get("cmd", "")).strip()
    battle = await battles.player_ability(session, character, cmd)
    return CommandResult(message=await _battle_message(session, battle))


# --- этап 6: крафт и склад ---------------------------------------------------------


async def cmd_recipes(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    recipes = await crafting.list_recipes(session, character)
    if not recipes:
        return CommandResult(message="Нет доступных рецептов.")
    lines = []
    for r in recipes:
        mark = "готов" if r["can_craft"] else "нет материалов"
        lines.append(
            f"{r['code']}: {r['name']} → {r['result']['qty']}× {r['result']['code']} "
            f"({r['duration_sec']}с) [{mark}]"
        )
        if r["missing"]:
            lines.append("   не хватает: " + ", ".join(f"{m['lack']}× {m['code']}" for m in r["missing"]))
    jobs = await crafting.running_jobs(session, character)
    if jobs:
        lines.append(f"Занято слотов: {len(jobs)}/{character.craft_slots}.")
    return CommandResult(message="Рецепты:\n" + "\n".join(lines))


async def cmd_craft(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("bad_args", "Формат: craft <код_рецепта>")
    job = await crafting.start_craft(session, character, code)
    return CommandResult(
        message=f"Производство «{code}» запущено (готово через {int((job.finishes_at - job.started_at).total_seconds())}с)."
    )


async def cmd_crafting(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    jobs = await crafting.running_jobs(session, character)
    if not jobs:
        return CommandResult(message="Нет активных заказов.")
    lines = [f"Слоты: {len(jobs)}/{character.craft_slots}"]
    for idx, job in enumerate(jobs, start=1):
        recipe = await session.get(RecipeTemplate, job.recipe_id)
        lines.append(f"{idx}. {recipe.name if recipe else '?'} — до {job.finishes_at:%H:%M:%S}")
    lines.append("Отмена: craftcancel <номер>")
    return CommandResult(message="\n".join(lines))


async def cmd_craft_cancel(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    index = int(args.get("index", 0))
    text = await crafting.cancel_craft(session, character, index)
    return CommandResult(message=text)


async def cmd_store(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not code or qty <= 0:
        raise DomainError("bad_args", "Формат: store <код> <количество>")
    await crafting.store_item(session, character, code, qty)
    return CommandResult(message=f"Перемещено на склад: {qty}× {code}.")


async def cmd_take(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not code or qty <= 0:
        raise DomainError("bad_args", "Формат: take <код> <количество>")
    await crafting.take_item(session, character, code, qty)
    return CommandResult(message=f"Взято со склада: {qty}× {code}.")


# --- этап 7: экономика -------------------------------------------------------------


async def cmd_wallet(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    eon = await economy.get_wallet(session, character.id, economy.EON)
    quant = await economy.get_wallet(session, character.id, economy.QUANT)
    return CommandResult(
        message=(
            f"Эоны: {eon.balance} (зарезервировано {eon.reserved})\n"
            f"Кванты: {quant.balance} (зарезервировано {quant.reserved})"
        )
    )


async def cmd_send(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    name = str(args.get("name", "")).strip()
    amount = int(args.get("amount", 0))
    if not name or amount <= 0:
        raise DomainError("bad_args", "Формат: send <имя> <кванты>")
    recipient = await economy.find_recipient(session, name)
    transfer = await economy.create_transfer(session, character, recipient, quant=amount)
    if transfer.status == "pending_confirmation":
        return CommandResult(message=f"Предложение отправлено «{recipient.name}» — ждёт подтверждения.")
    return CommandResult(message=f"Отправлено {amount} Квантов игроку «{recipient.name}».")


async def cmd_senditem(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    name = str(args.get("name", "")).strip()
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not name or not code or qty <= 0:
        raise DomainError("bad_args", "Формат: senditem <имя> <код> <количество>")
    recipient = await economy.find_recipient(session, name)
    transfer = await economy.create_transfer(
        session, character, recipient, items=[{"code": code, "qty": qty}]
    )
    if transfer.status == "pending_confirmation":
        return CommandResult(message=f"Предложение отправлено «{recipient.name}» — ждёт подтверждения.")
    return CommandResult(message=f"Отправлено {qty}× {code} игроку «{recipient.name}».")


async def cmd_inbox(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    pending = await economy.pending_transfers(session, character)
    if not pending:
        return CommandResult(message="Входящих предложений нет.")
    lines = []
    for transfer in pending:
        lines.append(
            f"{transfer.id.hex[:8]}: от «{transfer.from_id}» — "
            f"{transfer.payload.get('quant', 0)} Квантов, "
            f"{sum(int(i['qty']) for i in transfer.payload.get('items', []))} предметов. "
            f"confirm {transfer.id.hex[:8]} | decline {transfer.id.hex[:8]}"
        )
    return CommandResult(message="Входящие предложения:\n" + "\n".join(lines))


async def cmd_confirm(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    short_id = str(args.get("id", "")).strip()
    await economy.confirm_transfer(session, character, short_id)
    return CommandResult(message="Передача подтверждена и исполнена.")


async def cmd_sell(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    price = int(args.get("price", 0))
    buyout = args.get("buyout")
    lot = await market.create_lot(
        session, character,
        item_code=code, qty=qty, min_price=price,
        buyout=int(buyout) if buyout else None,
    )
    return CommandResult(
        message=f"Лот создан: {lot.qty}× {code}, мин. {price} Квантов. ID: {lot.id.hex[:8]}"
    )


async def cmd_lots(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lots = await market.list_lots(session)
    if not lots:
        return CommandResult(message="Активных лотов нет.")
    lines = []
    for lot in lots:
        mine = " (ваш)" if lot.seller_id == character.id else ""
        top = f", ставка {lot.top_bid}" if lot.top_bid else ""
        lines.append(
            f"{lot.id.hex[:8]}: {lot.qty}× {lot.item_code}, мин {lot.min_price_quant}{top}{mine}. "
            f"bid {lot.id.hex[:8]} <цена>"
        )
    return CommandResult(message="Аукцион:\n" + "\n".join(lines))


async def cmd_bid(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lot_id = str(args.get("lot_id", "")).strip()
    amount = int(args.get("amount", 0))
    lot = await market.place_bid(session, character, lot_id, amount)
    status = "продано с выкупом" if lot.status == "sold" else "ставка принята"
    return CommandResult(message=f"{status}: {lot.item_code}, ваша цена {amount}.")


async def cmd_lot_cancel(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lot_id = str(args.get("lot_id", "")).strip()
    text = await market.cancel_lot(session, character, lot_id)
    return CommandResult(message=text)


async def cmd_market(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    side = str(args.get("side", "")).strip()
    if side:
        amount = int(args.get("amount", 0))
        price = int(args.get("price", 0))
        order = await market.place_eon_order(
            session, character, side=side, amount=amount, price=price
        )
        return CommandResult(
            message=f"Ордер {side} размещён: {amount} Эонов по {price} Квантов. ID: {order.id.hex[:8]}"
        )
    reference = int(await stores.get_param(session, "market.reference_price_quant", 1000))
    return CommandResult(
        message=(
            f"Внутренний рынок Эонов. Ориентир: 1 Эон ≈ {reference} Квантов.\n"
            "market buy <эоны> <цена> — купить Эоны за Кванты\n"
            "market sell <эоны> <цена> — продать Эоны за Кванты\n"
            "Сведение ордеров — автоматически (worker)."
        )
    )


async def cmd_shop(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    await _load(session, ctx)
    offers = await market.list_offers(session)
    if not offers:
        return CommandResult(message="Магазин пуст.")
    lines = [f"{o.code}: {o.kind} — {o.price_eon} Эонов" for o in offers]
    lines.append("Покупка: buy <код>")
    return CommandResult(message="Магазин:\n" + "\n".join(lines))


async def cmd_buy(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    purchase = await market.buy_offer(session, character, code)
    return CommandResult(message=f"Покупка «{code}» оплачена ({purchase.status}).")


async def cmd_quests(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    text = await quests_mod.describe_quests(session, character)
    now = datetime.now(UTC)
    events = (
        await session.scalars(
            sa.select(FactionEvent).where(
                FactionEvent.status == "active", FactionEvent.ends_at > now
            )
        )
    ).all()
    if events:
        lines = [text, "", "Фракционные события:"]
        for event in events:
            goal = (event.config or {}).get("goal", {})
            contribution = await session.scalar(
                sa.select(
                    sa.func.coalesce(sa.func.sum(FactionEventParticipant.contribution), 0)
                ).where(FactionEventParticipant.event_id == event.id)
            )
            total = int(contribution or 0)
            lines.append(
                f"— {event.name}: общий прогресс {total}/{int(goal.get('count', 0))} "
                f"(до {event.ends_at:%d.%m %H:%M})"
            )
        text = "\n".join(lines)
    return CommandResult(message=text)


# --- разбор текста (общий для адаптеров) --------------------------------------

def split_text_command(text: str) -> tuple[str, str]:
    """'start Иван azimuth' -> ('start', 'Иван azimuth')"""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def parse_command(text: str) -> tuple[str, dict[str, Any]]:
    """Текст игрока -> (команда, аргументы)."""
    cmd, rest = split_text_command(text)
    if cmd in ("start", "начать"):
        return cmd, {"text": rest} if rest else {}
    if cmd == "choice":
        tokens = rest.split()
        return cmd, {"key": tokens[0]} if tokens else {}
    if cmd == "use":
        tokens = rest.split()
        return cmd, {"code": tokens[0]} if tokens else {}
    if cmd in ("install", "uninstall"):
        tokens = rest.split()
        return cmd, {"code": tokens[0]} if tokens else {}
    if cmd == "train":
        tokens = rest.split()
        if len(tokens) >= 2:
            return cmd, {"code": tokens[0], "xp": int(tokens[1])}
        return cmd, {"code": tokens[0]} if tokens else {}
    if cmd in ("store", "take"):
        tokens = rest.split()
        if len(tokens) >= 2:
            return cmd, {"code": tokens[0], "qty": int(tokens[1])}
        return cmd, {"code": tokens[0]} if tokens else {}
    if cmd in ("craft", "craftcancel"):
        tokens = rest.split()
        if cmd == "craftcancel" and tokens:
            return cmd, {"index": int(tokens[0])}
        return cmd, {"code": tokens[0]} if tokens else {}
    # этап 7: экономика
    if cmd == "send":
        tokens = rest.split()
        if len(tokens) >= 2:
            return cmd, {"name": " ".join(tokens[:-1]), "amount": int(tokens[-1])}
        return cmd, {"name": tokens[0]} if tokens else {}
    if cmd == "senditem":
        tokens = rest.split()
        if len(tokens) >= 3:
            return cmd, {
                "name": " ".join(tokens[:-2]),
                "code": tokens[-2],
                "qty": int(tokens[-1]),
            }
        return cmd, {}
    if cmd in ("confirm", "decline", "lotcancel", "marketcancel"):
        tokens = rest.split()
        return cmd, {"id": tokens[0]} if tokens else {}
    if cmd == "sell":
        tokens = rest.split()
        if len(tokens) >= 3:
            args = {"code": tokens[0], "qty": int(tokens[1]), "price": int(tokens[2])}
            if len(tokens) >= 4:
                args["buyout"] = int(tokens[3])
            return cmd, args
        return cmd, {}
    if cmd == "bid":
        tokens = rest.split()
        if len(tokens) >= 2:
            return cmd, {"lot_id": tokens[0], "amount": int(tokens[1])}
        return cmd, {}
    if cmd == "market":
        tokens = rest.split()
        if len(tokens) >= 3:
            return cmd, {"side": tokens[0], "amount": int(tokens[1]), "price": int(tokens[2])}
        return cmd, {}
    if cmd == "buy":
        tokens = rest.split()
        return cmd, {"code": tokens[0]} if tokens else {}
    if cmd == "inv":
        tokens = rest.split()
        if tokens and tokens[0] in ("ship", "warehouse", "waiting"):
            return cmd, {"kind": tokens[0]}
        return cmd, {}
    return cmd, {"rest": rest} if rest else {}


ACCOUNT_COMMANDS: dict[str, Any] = {
    "start": cmd_start,
    "factions": cmd_factions,
    "начать": cmd_start,
}

CHARACTER_COMMANDS: dict[str, Any] = {
    "me": cmd_me,
    "inv": cmd_inv,
    "inventory": cmd_inv,
    "ship": cmd_ship,
    # этап 3: экспедиции
    "exp": cmd_exp,
    "expedition": cmd_exp,
    "launch": cmd_launch,
    "вылет": cmd_launch,
    "go": cmd_go,
    "дальше": cmd_go,
    "choice": cmd_choice,
    "decline": cmd_decline,
    "return": cmd_return,
    "вернуться": cmd_return,
    # этап 4: бой
    "attack": cmd_attack,
    "атака": cmd_attack,
    "use": cmd_use,
    "retreat": cmd_retreat,
    "battle": cmd_battle,
    "бой": cmd_battle,
    "blog": cmd_battle_log,
    # этап 5: модули и развитие
    "mods": cmd_mods,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "train": cmd_train,
    "resetbuild": cmd_resetbuild,
    "ability": cmd_ability,
    # этап 6: крафт и склад
    "recipes": cmd_recipes,
    "craft": cmd_craft,
    "crafting": cmd_crafting,
    "craftcancel": cmd_craft_cancel,
    "store": cmd_store,
    "take": cmd_take,
    # этап 7: экономика
    "wallet": cmd_wallet,
    "send": cmd_send,
    "senditem": cmd_senditem,
    "inbox": cmd_inbox,
    "confirm": cmd_confirm,
    "sell": cmd_sell,
    "lots": cmd_lots,
    "bid": cmd_bid,
    "lotcancel": cmd_lot_cancel,
    "market": cmd_market,
    "shop": cmd_shop,
    "buy": cmd_buy,
    # этап 8: задания и фракционные события
    "quests": cmd_quests,
}
