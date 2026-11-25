"""VK-клавиатуры: структура JSON и смена меню по состоянию игрока."""
import random
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import battles, characters, expeditions
from app.keyboards import keyboard_for_character, vk_keyboard
from app.models_game import Character
from tests.conftest import requires_db


def test_vk_keyboard_structure() -> None:
    kb = vk_keyboard([["attack", "shot"], ["retreat"]])
    assert kb["one_time"] is False and kb["inline"] is False
    assert len(kb["buttons"]) == 2
    first = kb["buttons"][0][0]
    assert first["action"]["type"] == "text"
    assert first["action"]["label"] == "attack"
    assert first["color"] == "positive"  # боевые — зелёным
    retreat = kb["buttons"][1][0]
    assert retreat["color"] == "negative"


async def _character(session: AsyncSession, seeded: bool) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Кнопки{uuid.uuid4().hex[:6]}", faction_code="helion"
    )
    from app.models_game import Ship

    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 100
    await session.flush()
    return character


def _labels(kb: dict[str, Any]) -> list[str]:
    return [b["action"]["label"] for row in kb["buttons"] for b in row]


@requires_db
async def test_port_menu_by_default(session, seeded):
    character = await _character(session, seeded)
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    labels = _labels(kb)
    assert {"me", "inv", "ship", "launch", "quests", "wallet"} <= set(labels)


@requires_db
async def test_expedition_menu_and_battle_keyboard(session, seeded):
    character = await _character(session, seeded)
    # модуль ставим в порту (в экспедиции установка запрещена)
    from app import modules

    await modules.install_module(session, character, "overclock_core")

    expedition = await expeditions.launch_expedition(session, character)
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    assert {"go", "exp", "return"} <= set(_labels(kb))

    # кнопка способности появляется, только если модуль установлен
    await battles.start_battle(
        session, character, enemy_code="pirate_scout", expedition=expedition
    )
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    labels = set(_labels(kb))
    assert {"attack", "shot", "retreat"} <= labels
    assert "go" not in labels  # в бою экспедиционного меню нет


@requires_db
async def test_interactive_event_shows_choice_buttons(session, seeded):
    character = await _character(session, seeded)
    await expeditions.launch_expedition(session, character)
    for _ in range(40):
        event, _ = await expeditions.proceed(session, character, rng=random.Random(3))
        if event is None:
            pytest.fail("топливо кончилось раньше события с выбором")
        if event.snapshot["kind"] == "interactive":
            break
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    labels = _labels(kb)
    assert "1" in labels and "2" in labels  # варианты выбора — кнопками
    assert "decline" in labels
    assert "go" not in labels
