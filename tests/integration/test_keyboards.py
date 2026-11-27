"""VK-клавиатуры: структура JSON и смена меню по состоянию игрока."""
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import battles, characters, expeditions
from app.keyboards import keyboard_for_character, vk_keyboard
from app.models_game import Character, ExpeditionEvent
from tests.conftest import requires_db


async def _go(session, character, expedition, rng=None) -> ExpeditionEvent | None:
    """«Исследовать» + мгновенное прибытие события (в обход задержки пути)."""
    rng = rng or random.Random(1)
    await expeditions.proceed(session, character, rng=rng)
    await session.refresh(expedition)
    if expedition.status != "active" or expedition.next_event_at is None:
        return None
    next_seq = expedition.queue_pos  # seq события, которое создаст arrive_event
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.arrive_event(session, {"expedition_id": str(expedition.id)}, rng=rng)
    # None, если событие не создано (автовозврат по топливу)
    found: ExpeditionEvent | None = await session.scalar(
        sa.select(ExpeditionEvent).where(
            ExpeditionEvent.expedition_id == expedition.id,
            ExpeditionEvent.seq == next_seq,
        )
    )
    return found


def test_vk_keyboard_structure() -> None:
    kb = vk_keyboard([["attack", "shot"], ["retreat"]])
    assert kb["one_time"] is False and kb["inline"] is False
    assert len(kb["buttons"]) == 2
    first = kb["buttons"][0][0]
    assert first["action"]["type"] == "text"
    assert first["action"]["label"] == "Атака"
    assert first["color"] == "positive"  # боевые — зелёным
    retreat = kb["buttons"][1][0]
    assert retreat["action"]["label"] == "Отступить"
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
async def test_onboarding_keyboards(session, seeded):
    """Клавиатуры шагов онбординга: Начать → Продолжить → фракции → предпросмотр."""
    from app.keyboards import keyboard_for_account
    from app.models_game import Account

    account = Account()
    session.add(account)
    await session.flush()

    kb = await keyboard_for_account(session, account)
    assert _labels(kb) == ["Начать"]

    account.onboarding = {"step": "intro"}
    kb = await keyboard_for_account(session, account)
    assert _labels(kb) == ["Продолжить"]

    account.onboarding = {"step": "intro2"}
    kb = await keyboard_for_account(session, account)
    labels = _labels(kb)
    assert "Вольная Лига" in labels and "Синод Эонов" in labels
    payloads = [b["action"]["payload"] for row in kb["buttons"] for b in row]
    assert any("join lumen" in p for p in payloads)

    account.onboarding = {"step": "preview", "preview": "lumen"}
    kb = await keyboard_for_account(session, account)
    assert _labels(kb) == ["Выбрать эту фракцию", "◀ К списку фракций"]


@requires_db
async def test_port_halls_navigation(session, seeded):
    """Залы космопорта: основной (вход после экспедиции), мастерская, штаб."""
    character = await _character(session, seeded)

    # основной зал — по умолчанию
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    assert rows == [["Экспедиция", "О игроке"], ["Мастерская", "Штаб"]]

    # мастерская: навигация + кнопки крафта доступных рецептов (вместо кодов)
    character.hall = "workshop"
    await session.flush()
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    assert rows == [
        ["Модули", "Крафт"],
        ["Инвентарь", "О корабле"],
        ["Создать «Нанопластырь»"],
        ["Создать «Ремкомплект»"],
        ["Создать «Переработка триния»"],
        ["В основной зал"],
    ]

    # штаб: задания/магазин + кнопка принятия доступного задания (tutorial_1)
    character.hall = "hq"
    await session.flush()
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    assert rows == [
        ["Задания", "Магазин"],
        ["Принять «Первый вылет»"],
        ["В основной зал"],
    ]


@requires_db
async def test_expedition_menu_and_battle_keyboard(session, seeded):
    character = await _character(session, seeded)
    # модуль ставим в порту (в экспедиции установка запрещена)
    from app import modules

    await modules.install_module(session, character, "overclock_core")

    expedition = await expeditions.launch_expedition(session, character)
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    # свободное плавание: [Исследовать][Залатать] / [Инфо][В космопорт]
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    assert rows == [["Исследовать", "Залатать"], ["Инфо", "В космопорт"]]

    # во время ремонта — только «Инфо»
    from app.models_game import Ship

    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.current_hull = 40
    await session.flush()
    await expeditions.start_patch(session, character)
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    assert _labels(kb) == ["Инфо"]
    # завершаем ремонт напрямую — снова полное меню
    expedition.patch_eta = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.patch_complete(session, {"expedition_id": str(expedition.id)})
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    assert rows == [["Исследовать", "Залатать"], ["Инфо", "В космопорт"]]

    # в пути между событиями — только «Инфо»
    await expeditions.proceed(session, character, rng=random.Random(1))
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    assert _labels(kb) == ["Инфо"]
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.arrive_event(session, {"expedition_id": str(expedition.id)})
    await session.refresh(expedition)
    if expedition.status == "active":
        last = await session.scalar(
            sa.select(ExpeditionEvent)
            .where(ExpeditionEvent.expedition_id == expedition.id)
            .order_by(ExpeditionEvent.seq.desc())
            .limit(1)
        )
        if last is not None and last.status == "offered":
            await expeditions.decline_event(session, character)
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    rows = [[b["action"]["label"] for b in row] for row in kb["buttons"]]
    if expedition.status == "active":
        assert rows == [["Исследовать", "Залатать"], ["Инфо", "В космопорт"]]

    # кнопка способности появляется, только если модуль установлен
    await battles.start_battle(
        session, character, enemy_code="pirate_scout", expedition=expedition
    )
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    labels = set(_labels(kb))
    assert {"Атака", "shot", "Отступить", "Ремонт"} <= labels
    assert "go" not in labels  # в бою экспедиционного меню нет


@requires_db
async def test_interactive_event_shows_choice_buttons(session, seeded):
    character = await _character(session, seeded)
    expedition = await expeditions.launch_expedition(session, character)
    for _ in range(40):
        event = await _go(session, character, expedition, rng=random.Random(3))
        if event is None:
            pytest.fail("топливо кончилось раньше события с выбором")
        if event.snapshot["kind"] == "interactive":
            break
    kb = await keyboard_for_character(session, character.id)
    assert kb is not None
    labels = _labels(kb)
    # кнопки несут ТЕКСТ вариантов события (payload — команда choice)
    payloads = [b["action"]["payload"] for row in kb["buttons"] for b in row]
    assert any("choice 1" in p for p in payloads)
    assert any("choice 2" in p for p in payloads)
    assert "Пропустить" in labels or "Проскочить" in labels  # отказ (обычное/боевое событие)
    assert "decline" in " ".join(payloads)
    assert "go" not in labels
