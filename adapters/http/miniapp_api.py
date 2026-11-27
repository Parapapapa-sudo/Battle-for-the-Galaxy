"""Mini App API (этап 9): профиль, инвентарь, развитие, боевой журнал.

Аутентификация MVP: dev-режим — заголовок X-Character-Id (только app_env != prod).
Прод: верификация подписанных launch params VK (решение C2, этап релиза).
"""
import uuid
from collections.abc import AsyncIterator
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import battles, characters, modules
from app.config import Settings
from app.db import session_factory, transactional
from app.models_game import Battle, Character, Expedition, Ship

router = APIRouter(prefix="/api", tags=["miniapp"])


async def _session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def current_character(  # noqa: B008 — Depends в аргументах (идиома FastAPI)
    request: Request,
    x_character_id: str = Header(default=""),
    x_vk_launch_params: str = Header(default=""),
    session: AsyncSession = Depends(_session),
) -> Character:
    settings: Settings = request.app.state.settings
    if x_vk_launch_params and settings.vk_app_secret:
        # прод: верификация подписанных launch params VK Mini App (решение C2)
        from adapters.vk.launch_params import verify_launch_params

        try:
            params = verify_launch_params(x_vk_launch_params, settings.vk_app_secret)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        vk_user_id = params.get("vk_user_id") or params.get("user_id")
        if not vk_user_id:
            raise HTTPException(status_code=401, detail="нет vk_user_id в launch params")
        # подпись валидна → пользователь настоящий: аккаунт создаём автоматически
        # (как бот при первом сообщении), иначе мини-апп требовал «привязки» вручную
        from app import characters as characters_app
        from app.db import transactional

        async with transactional(session):
            account, _ = await characters_app.get_or_create_account(
                session, platform="vk", platform_user_id=str(vk_user_id)
            )
        character = await session.scalar(
            sa.select(Character).where(
                Character.account_id == account.id, Character.status == "active"
            )
        )
        if character is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Персонаж ещё не создан. Напишите любое сообщение нашему "
                    "сообществу ВКонтакте и пройдите создание персонажа — "
                    "после этого мини-приложение откроется."
                ),
            )
        return character
    if settings.is_prod:
        raise HTTPException(status_code=501, detail="VK launch params auth — задайте VK_APP_SECRET")
    if not x_character_id:
        raise HTTPException(status_code=401, detail="X-Character-Id required (dev)")
    try:
        char_id = uuid.UUID(x_character_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bad character id") from exc
    character = await session.get(Character, char_id)
    if character is None or character.status != "active":
        raise HTTPException(status_code=404, detail="character not found")
    return character


@router.get("/me")
async def me(
    character: Character = Depends(current_character),
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        ship = await session.get(Ship, character.id)
        expedition = await session.scalar(
            sa.select(Expedition).where(
                Expedition.character_id == character.id,
                Expedition.status.in_(["active", "returning"]),
            )
        )
        return {
            "name": character.name,
            "xp_pool": character.xp_pool,
            "inventory_size": character.inventory_size,
            "ship": (
                {
                    "hull": ship.current_hull,
                    "malfunctions": ship.malfunctions,
                    "fuel": ship.fuel,
                    "status": ship.status,
                }
                if ship
                else None
            ),
            "expedition": (
                {"status": expedition.status, "depth": expedition.depth}
                if expedition
                else None
            ),
        }


@router.get("/inventory")
async def inventory(
    character: Character = Depends(current_character),
    kind: str = "ship",
) -> dict[str, Any]:
    if kind not in ("ship", "warehouse", "waiting"):
        raise HTTPException(status_code=400, detail="kind: ship|warehouse|waiting")
    async with session_factory() as session, transactional(session):
        inv, rows = await characters.list_inventory(session, character.id, kind=kind)
        from app.images import icon_url

        return {
            "kind": kind,
            "size_limit": inv.size_limit,
            "items": [
                {**r.__dict__, "icon": icon_url("items", r.code)} for r in rows
            ],
            # фон инвентаря: assets/icons/ui/inventory_bg.(png|jpg|webp) или null
            "bg": icon_url("ui", "inventory_bg"),
        }


@router.get("/mods")
async def mods(character: Character = Depends(current_character)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        return {"xp_pool": character.xp_pool, "modules": await modules.list_modules(session, character)}


@router.get("/items/{code}")
async def item_detail(code: str, character: Character = Depends(current_character)) -> dict[str, Any]:
    """Карточка предмета: свойства, сколько у игрока, доступные действия."""
    from app.models_game import ItemTemplate

    async with session_factory() as session, transactional(session):
        template = await session.scalar(
            sa.select(ItemTemplate).where(ItemTemplate.code == code)
        )
        if template is None:
            raise HTTPException(status_code=404, detail="предмет не найден")
        owned = 0
        for kind in ("ship", "warehouse"):
            _, rows = await characters.list_inventory(session, character.id, kind=kind)
            owned += sum(r.qty for r in rows if r.code == code)
        mod = next(
            (m for m in await modules.list_modules(session, character) if m["code"] == code),
            None,
        )
        actions: list[dict[str, str]] = []
        if template.category == "consumable" and owned > 0:
            actions.append({"cmd": f"use {code}", "label": "Использовать"})
        if mod is not None:
            if mod["installed"]:
                actions.append({"cmd": f"uninstall {code}", "label": "Снять с корабля"})
                actions.append({"cmd": f"train {code} 10", "label": "Прокачать (+10 опыта)"})
            elif owned > 0:
                actions.append({"cmd": f"install {code}", "label": "Установить на корабль"})
        from app.images import icon_url

        return {
            "code": template.code,
            "icon": icon_url("items", template.code),
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "rarity": template.rarity,
            "is_unique": template.is_unique,
            "sellable": template.sellable,
            "tradable": template.tradable,
            "stack_limit": template.stack_limit,
            "owned": owned,
            "module": (
                {"installed": mod["installed"], "level": mod["level"], "xp": mod["xp"]}
                if mod
                else None
            ),
            "actions": actions,
        }


@router.get("/equipment")
async def equipment(character: Character = Depends(current_character)) -> dict[str, Any]:
    """Оснащение: установленные модули (корабль) и способности (команда)."""
    async with session_factory() as session, transactional(session):
        from app.images import icon_url

        mods = await modules.list_modules(session, character)
        for m in mods:
            m["icon"] = icon_url("items", m["code"])
        installed = [m for m in mods if m["installed"]]
        return {
            "ship_slots": [m for m in installed if m["kind"] != "ability"],
            "crew_skills": [m for m in installed if m["kind"] == "ability"],
            "storage": [m for m in mods if not m["installed"]],
        }


@router.get("/shop")
async def shop(character: Character = Depends(current_character)) -> dict[str, Any]:
    """Витрина магазина: название, цена, вид (паки Эонов за ₽, подписки за Эоны)."""
    from app import market

    async with session_factory() as session, transactional(session):
        from app.images import icon_url

        offers = await market.list_offers(session)
        return {
            "offers": [
                {
                    "code": o.code,
                    "icon": icon_url("shop", o.code),
                    "kind": o.kind,
                    "title": market.offer_title(o, character.lang),
                    "price_rub": o.price_rub if o.kind == "pack" else None,
                    "price_eon": o.price_eon if o.kind == "subscription" else None,
                    "eons": int(o.payload.get("eons", 0)) if o.kind == "pack" else None,
                }
                for o in offers
            ]
        }


@router.get("/journal")
async def journal(character: Character = Depends(current_character)) -> dict[str, Any]:
    """Журнал: задания (активные и доступные), достижения (звания), репутация."""
    from app import quests as quests_mod
    from app.models_game import CharacterTitle, Faction, Reputation

    async with session_factory() as session, transactional(session):
        quests_out = []
        for state, quest in await quests_mod.active_quests(session, character):
            quests_out.append(
                {
                    "name": quest.name,
                    "description": quest.description,
                    "status": "active",
                    "progress": quests_mod.quest_progress_line(state, quest, character.lang),
                }
            )
        for _state, quest in await quests_mod.available_quests(session, character):
            quests_out.append(
                {"name": quest.name, "description": quest.description,
                 "status": "available", "progress": ""}
            )
        titles = (
            await session.scalars(
                sa.select(CharacterTitle).where(CharacterTitle.character_id == character.id)
            )
        ).all()
        reps = (
            await session.execute(
                sa.select(Faction.name, Reputation.value)
                .join(Reputation, Reputation.faction_id == Faction.id)
                .where(Reputation.character_id == character.id)
            )
        ).all()
        return {
            "quests": quests_out,
            "achievements": [{"code": t.title_code} for t in titles],
            "reputation": [{"faction": name, "value": value} for name, value in reps],
        }


@router.get("/stats")
async def stats(character: Character = Depends(current_character)) -> dict[str, Any]:
    """Статистика игрока: профиль, счётчики боёв/экспедиций, кошельки."""
    from app import economy
    from app.models_game import CharacterQuest, Faction

    async with session_factory() as session, transactional(session):
        faction = await session.get(Faction, character.faction_id)
        battles_total = int(
            await session.scalar(
                sa.select(sa.func.count()).select_from(Battle).where(
                    Battle.character_id == character.id
                )
            )
            or 0
        )
        battles_won = int(
            await session.scalar(
                sa.select(sa.func.count()).select_from(Battle).where(
                    Battle.character_id == character.id, Battle.status == "won"
                )
            )
            or 0
        )
        expeditions_total = int(
            await session.scalar(
                sa.select(sa.func.count()).select_from(Expedition).where(
                    Expedition.character_id == character.id
                )
            )
            or 0
        )
        quests_done = int(
            await session.scalar(
                sa.select(sa.func.count()).select_from(CharacterQuest).where(
                    CharacterQuest.character_id == character.id,
                    CharacterQuest.status == "completed",
                )
            )
            or 0
        )
        eon_wallet = await economy.get_wallet(session, character.id, economy.EON)
        quant_wallet = await economy.get_wallet(session, character.id, economy.QUANT)
        return {
            "name": character.name,
            "faction": faction.name if faction else "?",
            "created_at": f"{character.created_at:%d.%m.%Y}" if character.created_at else "?",
            "xp_pool": character.xp_pool,
            "battles_total": battles_total,
            "battles_won": battles_won,
            "expeditions_total": expeditions_total,
            "quests_completed": quests_done,
            "eon": eon_wallet.balance,
            "quant": quant_wallet.balance,
        }


@router.post("/action")
async def action(
    request: Request,
    body: dict[str, Any],
    character: Character = Depends(current_character),
) -> dict[str, Any]:
    """Выполнение игровой команды из мини-аппа (кнопки действий).

    Команда проходит тот же конвейер, что и чат: parse → bus → идемпотентность.
    """
    from app.config import get_settings
    from app.router import route_text_command

    cmd = str(body.get("cmd", "")).strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="пустая команда")
    settings = get_settings()
    platform_user_id: str | None = None
    x_vk_launch_params = request.headers.get("x-vk-launch-params", "")
    if x_vk_launch_params and settings.vk_app_secret:
        from adapters.vk.launch_params import verify_launch_params

        try:
            params = verify_launch_params(x_vk_launch_params, settings.vk_app_secret)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        platform_user_id = params.get("vk_user_id") or params.get("user_id")
    if platform_user_id is None:
        # dev-режим: любая привязка аккаунта персонажа
        from app.models_game import AccountIdentity

        async with session_factory() as session:
            platform_user_id = await session.scalar(
                sa.select(AccountIdentity.platform_user_id).where(
                    AccountIdentity.account_id == character.account_id
                )
            )
    if platform_user_id is None:
        raise HTTPException(status_code=400, detail="нет привязки платформы")
    result = await route_text_command(
        _bus(),
        session_factory,
        platform="vk",
        platform_user_id=str(platform_user_id),
        text=cmd,
        idempotency_key=f"miniapp:{uuid.uuid4()}",
    )
    return {"message": result.message}


def _bus() -> Any:
    from app.bus import CommandBus

    return CommandBus(session_factory)


@router.get("/battles/{battle_id}/log")
async def battle_log(
    battle_id: uuid.UUID, character: Character = Depends(current_character)
) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        battle = await session.get(Battle, battle_id)
        if battle is None or battle.character_id != character.id:
            raise HTTPException(status_code=404, detail="battle not found")
        turns = await battles.battle_journal(session, battle, limit=50)
        return {
            "status": battle.status,
            "turns": [
                {
                    "turn_no": t.turn_no,
                    "actor": t.actor,
                    "summary": t.summary,
                    "log": t.log,
                }
                for t in turns
            ],
        }


@router.get("/quests")
async def api_quests(
    session: AsyncSession = Depends(_session),
    character: Character = Depends(current_character),
) -> dict[str, Any]:
    """Прогресс заданий и фракционных событий (экран обучения Mini App)."""
    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from app import quests as quests_mod
    from app.models_game import FactionEvent, FactionEventParticipant

    rows = await quests_mod.active_quests(session, character)
    quests = [
        {
            "name": q.name,
            "description": q.description,
            "progress": [
                {
                    "type": o.get("type"),
                    "have": min(int(state.progress.get(str(i), 0)), int(o.get("count", 1))),
                    "need": int(o.get("count", 1)),
                }
                for i, o in enumerate(q.objectives)
            ],
        }
        for state, q in rows
    ]
    now = datetime.now(UTC)
    events = (
        await session.scalars(
            select(FactionEvent).where(
                FactionEvent.status == "active", FactionEvent.ends_at > now
            )
        )
    ).all()
    faction_events = []
    for event in events:
        goal = (event.config or {}).get("goal", {})
        total = int(
            await session.scalar(
                select(func.coalesce(func.sum(FactionEventParticipant.contribution), 0)).where(
                    FactionEventParticipant.event_id == event.id
                )
            )
            or 0
        )
        faction_events.append(
            {"name": event.name, "progress": total, "goal": int(goal.get("count", 0))}
        )
    return {"quests": quests, "faction_events": faction_events}
