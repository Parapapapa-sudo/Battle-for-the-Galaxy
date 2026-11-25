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
from app.models_game import AccountIdentity, Battle, Character, Expedition, Ship

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
        identity = await session.scalar(
            sa.select(AccountIdentity).where(
                AccountIdentity.platform == "vk",
                AccountIdentity.platform_user_id == str(vk_user_id),
            )
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="аккаунт VK не привязан")
        character = await session.scalar(
            sa.select(Character).where(
                Character.account_id == identity.account_id, Character.status == "active"
            )
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
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
