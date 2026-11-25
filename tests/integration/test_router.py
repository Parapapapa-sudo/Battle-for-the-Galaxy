"""Роутер команд (путь адаптера): identity → аккаунт → скоуп, idempotency по ключу платформы."""
import uuid

import pytest
import sqlalchemy as sa

from app import characters
from app.bus import CommandBus
from app.models_game import Character
from app.router import route_text_command
from tests.conftest import requires_db


def _key() -> str:
    return f"vk:1:{uuid.uuid4().hex[:12]}"


@requires_db
async def test_full_player_journey(session, seeded, sessions):
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"

    r1 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="factions", idempotency_key=_key(),
    )
    assert "azimuth" in r1.message and "lumen" in r1.message

    # start без фракции → запрос выбора
    r2 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Капитан Тестер", idempotency_key=_key(),
    )
    assert "Выберите фракцию" in r2.message

    # start с фракцией → создание
    r3 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Капитан Тестер helion", idempotency_key=_key(),
    )
    assert "создан" in r3.message

    # me — профиль
    r4 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="me", idempotency_key=_key(),
    )
    assert "Капитан Тестер" in r4.message and "Хелионский" in r4.message

    # inv — стартовый набор
    r5 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="inv", idempotency_key=_key(),
    )
    assert "Металлолом" in r5.message

    # ship — стартовый корабль
    r6 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="ship", idempotency_key=_key(),
    )
    assert "Топливо: 50" in r6.message

    # неизвестная команда
    r7 = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="warp nine", idempotency_key=_key(),
    )
    assert "Неизвестная команда" in r7.message


@requires_db
async def test_platform_idempotency_key_prevents_duplicate_character(session, seeded, sessions):
    """Повторная доставка того же update (тот же ключ) не создаёт второго персонажа."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    key = _key()

    r1 = await route_text_command(
        bus, sessions, platform="vk", platform_user_id=user,
        text="start Дубль azimuth", idempotency_key=key,
    )
    assert "создан" in r1.message

    r2 = await route_text_command(
        bus, sessions, platform="vk", platform_user_id=user,
        text="start Дубль azimuth", idempotency_key=key,
    )
    assert r2.message == r1.message  # сохранённый ответ, эффектов нет

    account, _ = await characters.get_or_create_account(
        session, platform="vk", platform_user_id=user
    )
    chars = (
        await session.scalars(
            sa.select(Character).where(Character.account_id == account.id)
        )
    ).all()
    assert len(chars) == 1


@requires_db
async def test_new_user_gets_usage_hint(session, seeded, sessions):
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="me", idempotency_key=_key(),
    )
    assert "start" in r.message  # подсказка создания персонажа


@requires_db
async def test_digit_button_routes_to_event_choice(session, seeded, sessions):
    """Регрессия: кнопки «1»/«2» с клавиатуры шлют боту цифру — роутер связывает
    её с вариантом активного события (иначе «Неизвестная команда: 1»)."""

    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Кнопочник Тест helion", idempotency_key=_key(),
    )
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="launch", idempotency_key=_key(),
    )

    # ищем интерактивное событие (кулдаун между событиями снимаем напрямую)
    from app.expeditions import get_ongoing_expedition

    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    character = await characters.get_active_character(session, account.id)
    assert character is not None

    for _ in range(40):
        r = await route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text="go", idempotency_key=_key(),
        )
        if "Отказ: decline" in r.message:  # интерактивное событие открыто
            break
        await route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text="decline", idempotency_key=_key(),
        )
        expedition = await get_ongoing_expedition(session, character.id)
        if expedition is not None:
            expedition.next_event_at = None
            await session.flush()
    else:
        pytest.fail("интерактивное событие не встретилось")

    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="1", idempotency_key=_key(),
    )
    assert "Неизвестная команда" not in r.message
