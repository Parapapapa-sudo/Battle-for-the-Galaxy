"""VK bots long poll цикл: обновления → команды → ответы.

Идемпотентность по обновлению: ключ vk:<peer>:<conversation_message_id> —
повторная доставка update платформой не дублирует эффект (тесты конвейера).
Ответ команды шлём напрямую; дополнительные уведомления — через outbox (worker).
"""
import asyncio
import contextlib
import json
import signal
import time
from typing import Any

import structlog

from adapters.vk.api import VkApi, VkRateLimited, VkSender
from app import characters, db
from app.bus import CommandBus
from app.config import get_settings
from app.logging import configure_logging
from app.router import route_text_command
from core.errors import DomainError
from worker.dispatcher import OutboxDispatcher, dispatcher_from_settings

log = structlog.get_logger(__name__)


async def pump_outbox(
    dispatcher: OutboxDispatcher,
    stop: asyncio.Event,
    *,
    bus: CommandBus,
    handlers: dict[str, Any],
    interval: float = 0.5,
) -> None:
    """Фоновая поддержка: таймеры (прибытие/крафт/ремонт) + доставка outbox.

    Бот обрабатывает таймеры сам — не только worker: прибытие не должно
    зависеть от отдельного процесса (claim атомарен, повторные запуски безопасны).
    """
    while not stop.is_set():
        try:
            await bus.run_due_timers(timer_handlers=handlers)
            await dispatcher.run_once()
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            log.exception("outbox_pump_error")
        await asyncio.sleep(interval)


async def run_bot() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.vk_bot_token or not settings.vk_group_id:
        raise SystemExit("Задайте VK_BOT_TOKEN и VK_GROUP_ID")

    api = VkApi(settings.vk_bot_token, api_version=settings.vk_api_version)
    bus = CommandBus(db.session_factory)

    # Единственный поллер сообщества: второй экземпляр бота не стартует
    # (иначе VK Long Poll дублирует события и игрокам уходят повторы ответов).
    lock_conn = await db.engine.connect()
    bot_lock = db.LeaderLock(lock_conn, key=db.LeaderLock.KEY_VK_BOT)
    if not await bot_lock.acquire():
        log.error("vk_bot_already_running", group=settings.vk_group_id)
        raise SystemExit(1)
    dispatcher = dispatcher_from_settings(
        db.session_factory, VkSender(db.session_factory, api), settings
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    from app.timer_handlers import build_timer_handlers

    handlers = await build_timer_handlers()
    pump = asyncio.create_task(pump_outbox(dispatcher, stop, bus=bus, handlers=handlers))
    log.info("vk_bot_started", group=settings.vk_group_id)

    lp = await api.get_long_poll_server(settings.vk_group_id)
    server, key, ts = lp["server"], lp["key"], lp["ts"]
    try:
        while not stop.is_set():
            try:
                data = await api.poll(server, key, ts)
            except Exception as exc:  # noqa: BLE001 — сеть плавает, просто ждём
                log.warning("vk_poll_error", error=str(exc))
                await asyncio.sleep(3)
                continue
            if data is None or "failed" in data:
                log.warning("vk_poll_reconnect", reason=data)
                lp = await api.get_long_poll_server(settings.vk_group_id)
                server, key = lp["server"], lp["key"]
                ts = data.get("ts") if (data and "ts" in data) else lp["ts"]
                continue
            ts = data["ts"]
            for update in data.get("updates", []):
                await _handle_update(bus, api, update)
    finally:
        stop.set()
        await pump
        await bot_lock.release()
        await lock_conn.close()
        await db.engine.dispose()
        log.info("vk_bot_stopped")


async def _keyboard_for_user(platform_user_id: str) -> dict[str, Any]:  # keyboard всегда есть
    """Меню-кнопки по состоянию игрока (read-only, ничего не создаёт)."""
    import sqlalchemy as sa

    from app.keyboards import keyboard_for_account, keyboard_for_character, keyboard_for_new_user
    from app.models_game import Account, AccountIdentity

    async with db.session_factory() as session, db.transactional(session):
        identity = await session.scalar(
            sa.select(AccountIdentity).where(
                AccountIdentity.platform == "vk",
                AccountIdentity.platform_user_id == platform_user_id,
            )
        )
        if identity is None:
            return await keyboard_for_new_user()
        character = await characters.get_active_character(session, identity.account_id)
        if character is None:
            account = await session.get(Account, identity.account_id)
            if account is None:
                return await keyboard_for_new_user()
            return await keyboard_for_account(session, account)
        return (await keyboard_for_character(session, character.id)) or (await keyboard_for_new_user())


class _RecentReplies:
    """Ключи vk-обновлений, на которые бот уже ответил (в процессе).

    VK может повторно доставить обработанное событие (несколько поллеров,
    сбой ts, реконнекты) — идемпотентность защищает от повторных ЭФФЕКТОВ,
    а это защищает от повторной ОТПРАВКИ ответа игроку.
    """

    def __init__(self, capacity: int = 10_000) -> None:
        self._keys: set[str] = set()
        self._order: list[str] = []
        self._capacity = capacity

    def seen(self, key: str) -> bool:
        if key in self._keys:
            return True
        self._keys.add(key)
        self._order.append(key)
        if len(self._order) > self._capacity:
            oldest = self._order.pop(0)
            self._keys.discard(oldest)
        return False


_REPLIES = _RecentReplies()

# Сообщения старше этого возраста — бэклог long poll (реконнекты, второй поллер
# со старым ts, повторные доставки VK), а не живые команды. Иначе «игра играет
# сама»: старые нажатия кнопок исполняются спустя часы после отправки.
STALE_UPDATE_SEC = 120


async def _send_with_retry(
    api: VkApi,
    user_id: int,
    text: str,
    keyboard: dict[str, Any] | None,
    *,
    attachment: str | None = None,
    attempts: int = 4,
) -> None:
    """Прямые ответы обходят outbox-пейсинг — при лимите VK (20 msg/с) ретраим с бэкоффом."""
    delay = 1.0
    for attempt in range(attempts):
        try:
            await api.send_message(user_id, text, keyboard=keyboard, attachment=attachment)
            return
        except VkRateLimited:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def _handle_update(bus: CommandBus, api: VkApi, update: dict[str, Any]) -> None:
    if update.get("type") != "message_new":
        return
    obj: dict[str, Any] = update.get("object", {})
    message = obj.get("message", obj)  # схема v5.199: object.message
    user_id = message.get("from_id")
    text = (message.get("text") or "").strip()
    # payload кнопки (JSON с командой) имеет приоритет: label локализован, команда — нет
    raw_payload = message.get("payload")
    if raw_payload:
        try:
            payload_cmd = json.loads(raw_payload).get("cmd")
            if payload_cmd:
                text = str(payload_cmd).strip()
        except (ValueError, AttributeError):
            pass
    if not user_id or not text:
        return
    msg_date = message.get("date")
    if msg_date is not None and time.time() - float(msg_date) > STALE_UPDATE_SEC:
        log.info(
            "vk_stale_update_skipped",
            user=user_id,
            age_sec=int(time.time() - float(msg_date)),
        )
        return
    key = f"vk:{user_id}:{message.get('conversation_message_id', message.get('id'))}"
    if _REPLIES.seen(key):
        log.info("vk_update_duplicate_skipped", key=key)
        return
    try:
        result = await route_text_command(
            bus,
            db.session_factory,
            platform="vk",
            platform_user_id=str(user_id),
            text=text,
            idempotency_key=key,
        )
        keyboard = await _keyboard_for_user(str(user_id))
        attachment = None
        if result.image_pack:
            from adapters.vk.images import attachment_for

            attachment = await attachment_for(
                api, db.session_factory, result.image_pack, int(user_id)
            )
        await _send_with_retry(
            api, int(user_id), result.message[:4000], keyboard, attachment=attachment
        )
    except DomainError as exc:
        keyboard = await _keyboard_for_user(str(user_id))
        await _send_with_retry(api, int(user_id), exc.message[:4000], keyboard)
    except Exception:  # noqa: BLE001 — одно плохое обновление не останавливает бота
        log.exception("vk_update_error", update=update)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
