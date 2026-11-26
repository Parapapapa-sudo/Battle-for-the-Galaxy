"""Реестр игровых команд (общий для VK- и консольного адаптера). Все тексты — i18n.

Разделение скоупов (ТЗ: последовательность команд):
- account-команды (start/factions) — до создания персонажа, lock на аккаунт;
- character-команды — после создания, lock на персонажа, язык из characters.lang.
"""
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.quests as quests_mod
from app import battles, characters, crafting, economy, expeditions, market, modules, stores
from app.bus import CommandContext, CommandResult
from app.locale import t
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

USAGE_NO_CHARACTER = t("usage_no_character")


# --- account scope ------------------------------------------------------------

async def cmd_factions(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    factions = await characters.list_factions(session)
    if not factions:
        return CommandResult(message=t("factions_empty"))
    lines = [f"{f.code} — {f.name}" for f in factions]
    return CommandResult(
        message=t("factions_title") + "\n" + "\n".join(lines),
        data={"factions": [{"code": f.code, "name": f.name} for f in factions]},
    )


async def cmd_start(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    assert ctx.account_id is not None
    text = str(args.get("text", "")).strip()

    name, faction_code = text, None
    parts = text.rsplit(maxsplit=1)
    if len(parts) == 2 and await characters.get_faction_by_code(session, parts[1]):
        name, faction_code = parts[0], parts[1]

    if not name:
        factions = await characters.list_factions(session)
        codes = ", ".join(f.code for f in factions) or t("start_no_factions")
        return CommandResult(message=t("start_usage", codes=codes))
    if faction_code is None:
        factions = await characters.list_factions(session)
        lines = [f"{f.code} — {f.name}" for f in factions]
        return CommandResult(
            message=t("start_choose", name=name, factions="\n".join(lines)),
            data={"need_faction": True, "name": name},
        )

    character = await characters.create_character(
        session, account_id=ctx.account_id, name=name, faction_code=faction_code
    )
    faction = await characters.get_faction_by_code(session, faction_code)
    return CommandResult(
        message=t(
            "start_created",
            name=character.name,
            faction=faction.name if faction else faction_code,
        ),
        data={"character_id": str(character.id)},
    )


# --- character scope ----------------------------------------------------------

async def _load(session: AsyncSession, ctx: CommandContext) -> Character:
    assert ctx.character_id is not None
    return await characters.load_character(session, ctx.character_id)


async def cmd_me(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    faction_row = await session.get(Faction, character.faction_id)
    rep = await session.scalar(
        sa.select(Reputation).where(
            Reputation.character_id == character.id, Reputation.is_active
        )
    )
    return CommandResult(
        message=t(
            "profile",
            lang,
            name=character.name,
            faction=faction_row.name if faction_row else "?",
            rep=rep.value if rep else 0,
            xp=character.xp_pool,
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
    lang = character.lang
    kind = str(args.get("kind", "ship"))
    if kind not in ("ship", "warehouse", "waiting"):
        raise DomainError("bad_kind", t("inv_bad_kind", lang))
    inventory, rows = await characters.list_inventory(session, character.id, kind)
    if not rows:
        return CommandResult(message=t("inv_empty", lang))
    limit = inventory.size_limit if inventory.size_limit is not None else "∞"
    lines = [t("inv_item", lang, qty=r.qty, name=r.name, code=r.code) for r in rows]
    return CommandResult(
        message=t("inv_header", lang, kind=kind, used=len(rows), limit=limit) + "\n" + "\n".join(lines),
        data={"kind": kind, "items": [r.__dict__ for r in rows]},
    )


async def cmd_ship(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    ship, template = await characters.get_ship(session, character.id)
    return CommandResult(
        message=t(
            "ship_card",
            lang,
            name=template.name,
            hull=ship.current_hull,
            max=max_hull(ship.base_max_hull, ship.malfunctions),
            mal=ship.malfunctions,
            fuel=ship.fuel,
            status=ship.status,
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
    lang = character.lang
    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    if expedition is None:
        return CommandResult(message=t("exp_not_started", lang))
    if expedition.status == "returning":
        return CommandResult(
            message=t(
                "exp_returning",
                lang,
                reason=expedition.return_reason,
                depth=expedition.depth,
                fuel=expedition.fuel_remaining,
            )
        )
    offered = await expeditions._current_offered_event(session, expedition)
    if offered is not None:
        return CommandResult(message=expeditions.describe_event(offered, lang=lang))
    from datetime import UTC, datetime

    if expedition.next_event_at is not None and expedition.next_event_at > datetime.now(UTC):
        wait = int((expedition.next_event_at - datetime.now(UTC)).total_seconds()) + 1
        return CommandResult(
            message=await expeditions.describe_travel(session, character, expedition)
            + "\n"
            + t("travel_eta", lang, wait=wait)
        )
    low = int(await stores.get_param(session, "danger.low_max", 3))
    high = int(await stores.get_param(session, "danger.high_min", 7))
    last = await session.scalar(
        sa.select(ExpeditionEvent)
        .where(ExpeditionEvent.expedition_id == expedition.id)
        .order_by(ExpeditionEvent.seq.desc())
        .limit(1)
    )
    text = t(
        "exp_active",
        lang,
        depth=expedition.depth,
        fuel=expedition.fuel_remaining,
        start=expedition.fuel_at_start,
    )
    if last is not None and last.status == "resolved":
        text += "\n" + expeditions.describe_event(last, lang=lang, low_max=low, high_min=high)
    text += "\n" + t("exp_hint", lang)
    return CommandResult(message=text)


async def cmd_launch(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    expedition = await expeditions.launch_expedition(session, character)
    return CommandResult(
        message=t("launch_started", character.lang, fuel=expedition.fuel_remaining),
        data={"expedition_id": str(expedition.id)},
    )


async def cmd_go(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    event, note = await expeditions.proceed(session, character)
    if event is None:
        return CommandResult(message=note)
    low = int(await stores.get_param(session, "danger.low_max", 3))
    high = int(await stores.get_param(session, "danger.high_min", 7))
    text = expeditions.describe_event(event, lang=lang, low_max=low, high_min=high)
    if note:
        text = note + "\n" + text
    return CommandResult(message=text)


async def cmd_choice(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    key = str(args.get("key", "")).strip()
    if not key:
        raise DomainError("bad_choice", t("choice_missing_arg", character.lang))
    event = await expeditions.resolve_choice(session, character, key)
    return CommandResult(message=expeditions.describe_event(event, lang=character.lang))


async def cmd_decline(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    short_id = str(args.get("id", "")).strip()
    if short_id:
        await economy.reject_transfer(session, character, short_id)
        return CommandResult(message=t("decline_transfer", lang))
    event = await expeditions.decline_event(session, character)
    return CommandResult(
        message=t("decline_event", lang, name=event.snapshot.get("name", "?"))
    )


async def cmd_return(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    await expeditions.request_return(session, character)
    return CommandResult(message=t("return_started", character.lang))


# --- этап 4: бой -----------------------------------------------------------------


async def _battle_message(
    session: AsyncSession, battle: Battle, *, header: bool = False, lang: str = "ru"
) -> str:
    """Экран боя. header=True — вступительный (характеристики сторон, ещё не ход 1);
    иначе — итог раунда: действия сверху, HP-блоки ниже. Финал — отдельное сообщение."""
    if battle.status in ("won", "lost"):
        return await battles.describe_battle_outcome(session, battle, lang)
    if header:
        return await battles.describe_battle(session, battle, lang=lang)
    return await battles.describe_round_result(session, battle, lang=lang)


async def cmd_attack(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.player_attack(session, character)
    return CommandResult(message=await _battle_message(session, battle, lang=character.lang))


async def cmd_use(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_item", t("craft_bad", character.lang))
    battle = await battles.player_use_item(session, character, code)
    return CommandResult(message=await _battle_message(session, battle, lang=character.lang))


async def cmd_retreat(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.player_retreat(session, character)
    return CommandResult(message=await _battle_message(session, battle, lang=character.lang))


async def cmd_battle(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    battle = await battles.get_active_battle(session, character.id)
    if battle is None:
        return CommandResult(message=t("battle_none", character.lang))
    return CommandResult(
        message=await _battle_message(session, battle, header=True, lang=character.lang)
    )


async def cmd_battle_log(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    battle = await battles.get_active_battle(session, character.id)
    if battle is None:
        return CommandResult(message=t("battle_none", lang))
    turns = await battles.battle_journal(session, battle, limit=10)
    lines = [t("blog_turn", lang, no=tr.turn_no, actor=tr.actor, summary=tr.summary) for tr in turns]
    for tr in turns:
        for entry in tr.log:
            lines.append(t("blog_detail", lang, entry=entry))
    return CommandResult(message=t("blog_header", lang) + "\n" + "\n".join(lines))


# --- этап 5: модули и развитие ----------------------------------------------------


async def cmd_mods(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    mods = await modules.list_modules(session, character)
    if not mods:
        return CommandResult(message=t("mods_empty", lang))
    lines = [t("mods_header", lang, xp=character.xp_pool, resets=character.build_resets)]
    for m in mods:
        status = (
            t("mods_status_slot", lang, slot=m["slot"])
            if m["installed"]
            else t("mods_status_inv", lang)
        )
        extra = ""
        if m["attack_bonus"]:
            extra = t("mods_extra_attack", lang, bonus=m["attack_bonus"])
        if m["ability"]:
            extra += t("mods_extra_ability", lang, cmd=m["ability"].get("combat_cmd"))
        lines.append(
            t(
                "mods_item",
                lang,
                kind=m["kind"],
                name=m["name"],
                code=m["code"],
                level=m["level"],
                xp=m["xp_invested"],
                status=status,
                extra=extra,
            )
        )
    lines.append(t("mods_hint", lang))
    return CommandResult(message="\n".join(lines))


async def cmd_install(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_module", t("module_missing", character.lang, cmd="install"))
    await modules.install_module(session, character, code)
    return CommandResult(message=t("install_ok", character.lang, code=code))


async def cmd_uninstall(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("no_module", t("module_missing", character.lang, cmd="uninstall"))
    await modules.uninstall_module(session, character, code)
    return CommandResult(message=t("uninstall_ok", character.lang, code=code))


async def cmd_train(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    xp = int(args.get("xp", 0))
    if not code or xp <= 0:
        raise DomainError("bad_args", t("train_bad", character.lang))
    await modules.train_module(session, character, code, xp)
    return CommandResult(
        message=t("train_ok", character.lang, xp=xp, code=code, pool=character.xp_pool)
    )


async def cmd_resetbuild(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    returned = await modules.reset_build(session, character)
    return CommandResult(
        message=t("reset_ok", character.lang, returned=returned, pool=character.xp_pool)
    )


async def cmd_ability(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    cmd = str(args.get("cmd", "")).strip()
    battle = await battles.player_ability(session, character, cmd)
    return CommandResult(message=await _battle_message(session, battle, lang=character.lang))


# --- этап 6: крафт и склад ---------------------------------------------------------


async def cmd_recipes(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    recipes = await crafting.list_recipes(session, character)
    if not recipes:
        return CommandResult(message=t("recipes_empty", lang))
    lines = []
    for r in recipes:
        mark = t("recipe_ready", lang) if r["can_craft"] else t("recipe_no_materials", lang)
        lines.append(
            t(
                "recipe_line",
                lang,
                code=r["code"],
                name=r["name"],
                qty=r["result"]["qty"],
                result=r["result"]["code"],
                duration=r["duration_sec"],
                mark=mark,
            )
        )
        if r["missing"]:
            missing = ", ".join(f"{m['lack']}× {m['code']}" for m in r["missing"])
            lines.append(t("recipe_missing", lang, list=missing))
    jobs = await crafting.running_jobs(session, character)
    if jobs:
        lines.append(t("recipes_slots", lang, used=len(jobs), total=character.craft_slots))
    return CommandResult(message=t("recipes_title", lang) + "\n" + "\n".join(lines))


async def cmd_craft(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    if not code:
        raise DomainError("bad_args", t("craft_bad", character.lang))
    job = await crafting.start_craft(session, character, code)
    return CommandResult(
        message=t(
            "craft_started",
            character.lang,
            code=code,
            seconds=int((job.finishes_at - job.started_at).total_seconds()),
        )
    )


async def cmd_crafting(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    jobs = await crafting.running_jobs(session, character)
    if not jobs:
        return CommandResult(message=t("crafting_empty", lang))
    lines = [t("crafting_slots", lang, used=len(jobs), total=character.craft_slots)]
    for idx, job in enumerate(jobs, start=1):
        recipe = await session.get(RecipeTemplate, job.recipe_id)
        lines.append(
            t("crafting_item", lang, idx=idx, name=recipe.name if recipe else "?", time=f"{job.finishes_at:%H:%M:%S}")
        )
    lines.append(t("crafting_hint", lang))
    return CommandResult(message="\n".join(lines))


async def cmd_craft_cancel(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    index = int(args.get("index", 0))
    text = await crafting.cancel_craft(session, character, index)
    return CommandResult(message=text)


async def cmd_store(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not code or qty <= 0:
        raise DomainError("bad_args", t("store_bad", lang))
    await crafting.store_item(session, character, code, qty)
    return CommandResult(message=t("store_ok", lang, qty=qty, code=code))


async def cmd_take(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not code or qty <= 0:
        raise DomainError("bad_args", t("take_bad", lang))
    await crafting.take_item(session, character, code, qty)
    return CommandResult(message=t("take_ok", lang, qty=qty, code=code))


# --- этап 7: экономика -------------------------------------------------------------


async def cmd_wallet(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    eon = await economy.get_wallet(session, character.id, economy.EON)
    quant = await economy.get_wallet(session, character.id, economy.QUANT)
    return CommandResult(
        message=t(
            "wallet",
            character.lang,
            eon=eon.balance,
            eon_r=eon.reserved,
            quant=quant.balance,
            quant_r=quant.reserved,
        )
    )


async def cmd_send(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    name = str(args.get("name", "")).strip()
    amount = int(args.get("amount", 0))
    if not name or amount <= 0:
        raise DomainError("bad_args", t("send_bad", lang))
    recipient = await economy.find_recipient(session, name)
    transfer = await economy.create_transfer(session, character, recipient, quant=amount)
    if transfer.status == "pending_confirmation":
        return CommandResult(message=t("send_pending", lang, name=recipient.name))
    return CommandResult(message=t("send_ok", lang, amount=amount, name=recipient.name))


async def cmd_senditem(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    name = str(args.get("name", "")).strip()
    code = str(args.get("code", "")).strip()
    qty = int(args.get("qty", 0))
    if not name or not code or qty <= 0:
        raise DomainError("bad_args", t("senditem_bad", lang))
    recipient = await economy.find_recipient(session, name)
    transfer = await economy.create_transfer(
        session, character, recipient, items=[{"code": code, "qty": qty}]
    )
    if transfer.status == "pending_confirmation":
        return CommandResult(message=t("senditem_pending", lang, name=recipient.name))
    return CommandResult(message=t("senditem_ok", lang, qty=qty, code=code, name=recipient.name))


async def cmd_inbox(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    pending = await economy.pending_transfers(session, character)
    if not pending:
        return CommandResult(message=t("inbox_empty", lang))
    lines = []
    for transfer in pending:
        lines.append(
            t(
                "inbox_item",
                lang,
                id8=transfer.id.hex[:8],
                sender=transfer.from_id,
                quant=transfer.payload.get("quant", 0),
                items=sum(int(i["qty"]) for i in transfer.payload.get("items", [])),
            )
        )
    return CommandResult(message=t("inbox_title", lang) + "\n" + "\n".join(lines))


async def cmd_confirm(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    short_id = str(args.get("id", "")).strip()
    await economy.confirm_transfer(session, character, short_id)
    return CommandResult(message=t("confirm_ok", character.lang))


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
        message=t(
            "sell_ok",
            character.lang,
            qty=lot.qty,
            code=code,
            price=price,
            id8=lot.id.hex[:8],
        )
    )


async def cmd_lots(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    lots = await market.list_lots(session)
    if not lots:
        return CommandResult(message=t("lots_empty", lang))
    lines = []
    for lot in lots:
        mine = t("lots_mine", lang) if lot.seller_id == character.id else ""
        top = t("lots_top", lang, top=lot.top_bid) if lot.top_bid else ""
        lines.append(
            t(
                "lots_item",
                lang,
                id8=lot.id.hex[:8],
                qty=lot.qty,
                code=lot.item_code,
                min=lot.min_price_quant,
                top=top,
                mine=mine,
            )
        )
    return CommandResult(message=t("lots_title", lang) + "\n" + "\n".join(lines))


async def cmd_bid(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lot_id = str(args.get("lot_id", "")).strip()
    amount = int(args.get("amount", 0))
    lot = await market.place_bid(session, character, lot_id, amount)
    key = "bid_buyout" if lot.status == "sold" else "bid_ok"
    return CommandResult(
        message=t(key, character.lang, code=lot.item_code, amount=amount)
    )


async def cmd_lot_cancel(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lot_id = str(args.get("lot_id", "")).strip()
    text = await market.cancel_lot(session, character, lot_id)
    return CommandResult(message=text)


async def cmd_market(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    side = str(args.get("side", "")).strip()
    if side:
        amount = int(args.get("amount", 0))
        price = int(args.get("price", 0))
        order = await market.place_eon_order(
            session, character, side=side, amount=amount, price=price
        )
        return CommandResult(
            message=t(
                "market_order",
                lang,
                side=side,
                amount=amount,
                price=price,
                id8=order.id.hex[:8],
            )
        )
    reference = int(await stores.get_param(session, "market.reference_price_quant", 1000))
    return CommandResult(message=t("market_help", lang, reference=reference))


async def cmd_shop(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    offers = await market.list_offers(session)
    if not offers:
        return CommandResult(message=t("shop_empty", lang))
    lines = [
        t("shop_item", lang, code=o.code, kind=o.kind, price=o.price_eon) for o in offers
    ]
    lines.append(t("shop_hint", lang))
    return CommandResult(message=t("shop_title", lang) + "\n" + "\n".join(lines))


async def cmd_buy(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    code = str(args.get("code", "")).strip()
    purchase = await market.buy_offer(session, character, code)
    return CommandResult(
        message=t("buy_ok", character.lang, code=code, status=purchase.status)
    )


async def cmd_patch(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    delay = await expeditions.start_patch(session, character)
    return CommandResult(message=t("patch_started", character.lang, delay=delay))


async def cmd_sync(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    from app.timer_handlers import sync_character_timers

    assert ctx.character_id is not None
    character = await _load(session, ctx)
    fired = await sync_character_timers(session, character.id)
    if not fired:
        return CommandResult(message=t("sync_empty", character.lang))
    return CommandResult(
        message=t("sync_fired", character.lang, names=", ".join(fired))
    )


async def cmd_quests(session: AsyncSession, ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    character = await _load(session, ctx)
    lang = character.lang
    text = t("quests_title", lang) + "\n" if await quests_mod.active_quests(session, character) else ""
    for state, quest in await quests_mod.active_quests(session, character):
        parts = []
        for idx, objective in enumerate(quest.objectives):
            need = int(objective.get("count", 1))
            have = min(int(state.progress.get(str(idx), 0)), need)
            parts.append(f"{objective.get('type')} {have}/{need}")
        text += t("quest_item", lang, name=quest.name, progress=", ".join(parts)) + "\n"
    now = datetime.now(UTC)
    events = (
        await session.scalars(
            sa.select(FactionEvent).where(
                FactionEvent.status == "active", FactionEvent.ends_at > now
            )
        )
    ).all()
    if events:
        lines = [t("fevents_title", lang)]
        for event in events:
            goal = (event.config or {}).get("goal", {})
            contribution = await session.scalar(
                sa.select(
                    sa.func.coalesce(sa.func.sum(FactionEventParticipant.contribution), 0)
                ).where(FactionEventParticipant.event_id == event.id)
            )
            total = int(contribution or 0)
            lines.append(
                t(
                    "fevent_item",
                    lang,
                    name=event.name,
                    total=total,
                    goal=int(goal.get("count", 0)),
                    until=f"{event.ends_at:%d.%m %H:%M}",
                )
            )
        text += "\n" + "\n".join(lines)
    return CommandResult(message=text.rstrip())


# --- разбор текста (общий для адаптеров) --------------------------------------

def split_text_command(text: str) -> tuple[str, str]:
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
    "attack": cmd_attack,
    "атака": cmd_attack,
    "use": cmd_use,
    "retreat": cmd_retreat,
    "battle": cmd_battle,
    "бой": cmd_battle,
    "blog": cmd_battle_log,
    "mods": cmd_mods,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "train": cmd_train,
    "resetbuild": cmd_resetbuild,
    "ability": cmd_ability,
    "recipes": cmd_recipes,
    "craft": cmd_craft,
    "crafting": cmd_crafting,
    "craftcancel": cmd_craft_cancel,
    "store": cmd_store,
    "take": cmd_take,
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
    "quests": cmd_quests,
    "sync": cmd_sync,
    # ремонт в экспедиции
    "patch": cmd_patch,
    "залатать": cmd_patch,
}
