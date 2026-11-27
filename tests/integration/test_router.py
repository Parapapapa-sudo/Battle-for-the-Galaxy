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
    assert "Капитан Тестер" in r4.message and "Астрийский Конкордат" in r4.message

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
async def test_new_user_gets_welcome(session, seeded, sessions):
    """Новичок (без персонажа) получает приветствие с призывом «Начать»."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="привет", idempotency_key=_key(),
    )
    assert "Начать" in r.message and "битву за галактику" in r.message


@requires_db
async def test_onboarding_full_flow(session, seeded, sessions):
    """Онбординг: приветствие → обзор → предыстория → фракция → имя текстом."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"

    async def send(text: str):
        return await route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text=text, idempotency_key=_key(),
        )

    r = await send("привет")
    assert "Начать" in r.message

    r = await send("intro")  # кнопка «Начать»
    assert "Твоя история начинается здесь" in r.message

    r = await send("начать")  # повторный ввод текстом — снова обзор
    assert "Твоя история начинается здесь" in r.message

    r = await send("intro2")  # кнопка «Продолжить»
    assert "Выбери фракцию" in r.message and "Конкордат" in r.message

    r = await send("join lumen")  # ПРОСМОТР фракции — выбор ещё не зафиксирован
    assert "Синод Эонов" in r.message and "Выбрать эту фракцию" in r.message
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    assert "faction" not in (account.onboarding or {})

    r = await send("pick lumen")  # подтверждение выбора
    assert "Фракция выбрана" in r.message and "имя капитана" in r.message

    # короткое имя не проходит валидацию — состояние выбора сохраняется
    r = await send("ab")
    assert "создан" not in r.message

    r = await send("Капитан Смит")  # имя свободным текстом
    assert "создан" in r.message and "Синод Эонов" in r.message
    assert "Экспедиция" in r.message  # подсказка первого шага

    # персонаж создан, онбординг очищен, фракция верная
    await session.refresh(account)
    character = await characters.get_active_character(session, account.id)
    assert character is not None and character.name == "Капитан Смит"
    assert account.onboarding == {}
    from app.models_game import Faction

    faction = await session.get(Faction, character.faction_id)
    assert faction is not None and faction.code == "lumen"


@requires_db
async def test_recipes_screen_human_readable(session, seeded, sessions):
    """Рецепты: названия вместо кодов, материалы есть/нужно, статус и команда."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Крафтер Тестов azimuth", idempotency_key=_key(),
    )
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="recipes", idempotency_key=_key(),
    )
    assert "«Нанопластырь» → 1× Нанопластырь (45 с)" in r.message
    assert "Материалы: Металлолом 3/3" in r.message  # стартовый лом покрывает рецепт
    assert "✅ Готов" in r.message
    assert "craft craft_nano_patch" not in r.message  # коды заменены кнопками в Мастерской
    assert "nano_patch (" not in r.message  # сырых кодов предметов нет


@requires_db
async def test_shop_display_and_buy_flow(session, seeded, sessions):
    """Магазин: человекочитаемая витрина; паки Эонов — счёт в рублях, подписки — за Эоны."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Покупатель Тестов azimuth", idempotency_key=_key(),
    )

    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="shop", idempotency_key=_key(),
    )
    assert "💎 Эоны (оплата в рублях)" in r.message
    assert "100 Эонов за 100 ₽" in r.message
    assert "Подписка на 7 дн. — 99 Эонов" in r.message
    assert "subscription —" not in r.message  # сырых кодов/видов больше нет

    # пак Эонов → платёжный заказ со ссылкой на оплату (не списание Эонов)
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="buy eon_pack_100", idempotency_key=_key(),
    )
    assert "Счёт создан: 100 Эонов за 100 ₽" in r.message
    assert "Оплатить:" in r.message

    # подписка за Эоны: без средств — понятная ошибка
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="buy sub_7", idempotency_key=_key(),
    )
    assert "Недостаточно" in r.message

    # неизвестное предложение
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="buy no_such_offer", idempotency_key=_key(),
    )
    assert "не найдено" in r.message


@requires_db
async def test_ui_command_reloads_menu(session, seeded, sessions):
    """«ui» — перезагрузка интерфейса: работает и без персонажа, и с ним."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"

    # без персонажа
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="ui", idempotency_key=_key(),
    )
    assert "Меню обновлено" in r.message

    # с персонажем (русский алиас)
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Юай Тестов azimuth", idempotency_key=_key(),
    )
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="меню", idempotency_key=_key(),
    )
    assert "Меню обновлено" in r.message


@requires_db
async def test_hall_navigation_commands(session, seeded, sessions):
    """Залы космопорта: hall workshop/hq/main меняет зал; в экспедиции — нельзя."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Ходок Тестов azimuth", idempotency_key=_key(),
    )

    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall workshop", idempotency_key=_key(),
    )
    assert "Мастерская" in r.message
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    character = await characters.get_active_character(session, account.id)
    assert character is not None and character.hall == "workshop"

    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall main", idempotency_key=_key(),
    )
    assert "Основной зал" in r.message
    await session.refresh(character)
    assert character.hall == "main"

    # несуществующий зал
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall casino", idempotency_key=_key(),
    )
    assert "Неизвестный зал" in r.message

    # в экспедиции залы недоступны
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="launch", idempotency_key=_key(),
    )
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall hq", idempotency_key=_key(),
    )
    assert "только в порту" in r.message


@requires_db
async def test_page_command_turns_button_pages(session, seeded, sessions):
    """«Далее ▶»: команда page листает страницу кнопок зала; смена зала сбрасывает."""
    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="start Листатель Тестов azimuth", idempotency_key=_key(),
    )
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall workshop", idempotency_key=_key(),
    )
    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="page next", idempotency_key=_key(),
    )
    assert "Мастерская" in r.message  # повтор экрана зала с новой страницей кнопок
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    character = await characters.get_active_character(session, account.id)
    assert character is not None and character.hall_page == 1

    # смена зала сбрасывает страницу
    await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="hall main", idempotency_key=_key(),
    )
    await session.refresh(character)
    assert character.hall_page == 0


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

    # ищем интерактивное событие: go отправляет в путь, таймер доводим вручную,
    # срабатывание — через команду sync (как без worker'а вживую)
    from datetime import UTC, datetime, timedelta

    from app.expeditions import _current_offered_event, get_ongoing_expedition
    from app.models import Timer

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
        assert "путешествие" in r.message  # экран пути, событие ещё не прибыло
        expedition = await get_ongoing_expedition(session, character.id)
        assert expedition is not None
        expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.execute(
            sa.update(Timer)
            .where(Timer.type == "exp_event", Timer.status == "pending")
            .values(fires_at=expedition.next_event_at)
        )
        await session.flush()
        await route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text="sync", idempotency_key=_key(),
        )
        offered = await _current_offered_event(session, expedition)
        if offered is not None and offered.snapshot["kind"] == "interactive":
            break
        # автоматическое событие разрешилось само — идём дальше
    else:
        pytest.fail("интерактивное событие не встретилось")

    r = await route_text_command(
        bus, sessions, platform="console", platform_user_id=user,
        text="1", idempotency_key=_key(),
    )
    assert "Неизвестная команда" not in r.message
