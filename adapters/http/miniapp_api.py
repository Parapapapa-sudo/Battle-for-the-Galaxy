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
        return {
            "kind": kind,
            "size_limit": inv.size_limit,
            "items": [r.__dict__ for r in rows],
        }


@router.get("/mods")
async def mods(character: Character = Depends(current_character)) -> dict[str, Any]:
    async with session_factory() as session, transactional(session):
        return {"xp_pool": character.xp_pool, "modules": await modules.list_modules(session, character)}


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
