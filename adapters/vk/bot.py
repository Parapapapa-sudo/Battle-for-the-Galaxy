"""VK bots long poll цикл: обновления → команды → ответы.

Идемпотентность по обновлению: ключ vk:<peer>:<conversation_message_id> —
повторная доставка update платформой не дублирует эффект (тесты конвейера).
Ответ команды шлём напрямую; дополнительные уведомления — через outbox (worker).
"""
import asyncio
import contextlib
import json
import signal
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


async def pump_outbox(dispatcher: OutboxDispatcher, stop: asyncio.Event, interval: float = 0.5) -> None:
    while not stop.is_set():
        try:
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
    dispatcher = dispatcher_from_settings(
        db.session_factory, VkSender(db.session_factory, api), settings
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    pump = asyncio.create_task(pump_outbox(dispatcher, stop))
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
        await db.engine.dispose()
        log.info("vk_bot_stopped")


async def _keyboard_for_user(platform_user_id: str) -> dict[str, Any]:  # keyboard всегда есть
    """Меню-кнопки по состоянию игрока (read-only, ничего не создаёт)."""
    import sqlalchemy as sa

    from app.keyboards import keyboard_for_character, keyboard_for_new_user
    from app.models_game import AccountIdentity

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
            return await keyboard_for_new_user()
        return (await keyboard_for_character(session, character.id)) or (await keyboard_for_new_user())


async def _send_with_retry(
    api: VkApi, user_id: int, text: str, keyboard: dict[str, Any] | None, *, attempts: int = 4
) -> None:
    """Прямые ответы обходят outbox-пейсинг — при лимите VK (20 msg/с) ретраим с бэкоффом."""
    delay = 1.0
    for attempt in range(attempts):
        try:
            await api.send_message(user_id, text, keyboard=keyboard)
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
    key = f"vk:{user_id}:{message.get('conversation_message_id', message.get('id'))}"
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
        await _send_with_retry(api, int(user_id), result.message[:4000], keyboard)
    except DomainError as exc:
        keyboard = await _keyboard_for_user(str(user_id))
        await _send_with_retry(api, int(user_id), exc.message[:4000], keyboard)
    except Exception:  # noqa: BLE001 — одно плохое обновление не останавливает бота
        log.exception("vk_update_error", update=update)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
