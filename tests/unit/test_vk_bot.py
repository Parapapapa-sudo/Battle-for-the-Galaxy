"""Бот VK: защита от переигровки бэклога long poll; выбор транспорта outbox в worker."""
import time
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.vk.api import VkApi, VkSender
from adapters.vk.bot import STALE_UPDATE_SEC, _handle_update
from app.bus import CommandBus
from app.config import Settings
from worker.main import _sender_from_settings


class _ApiStub:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(
        self, user_id: int, text: str, *, keyboard: dict[str, Any] | None = None
    ) -> int:
        self.sent.append((user_id, text))
        return 1


def _update(msg_date: int) -> dict[str, Any]:
    return {
        "type": "message_new",
        "object": {
            "message": {
                "from_id": 1,
                "text": "go",
                "conversation_message_id": 867,
                "date": msg_date,
            }
        },
    }


async def test_stale_update_is_skipped() -> None:
    """Сообщение старше STALE_UPDATE_SEC — бэклог: команда НЕ выполняется, ответа нет."""
    api = _ApiStub()
    stale = _update(int(time.time()) - STALE_UPDATE_SEC - 60)
    await _handle_update(cast(CommandBus, None), cast(VkApi, api), stale)
    assert api.sent == []


def test_worker_sender_uses_vk_when_configured() -> None:
    """Worker с настроенным VK доставляет outbox по-настоящему (иначе PrintSender)."""
    sessions = cast(async_sessionmaker[AsyncSession], None)
    vk_settings = Settings(vk_bot_token="token", vk_group_id=1)
    assert isinstance(_sender_from_settings(vk_settings, sessions), VkSender)

    plain = Settings(vk_bot_token=None, vk_group_id=None)
    assert not isinstance(_sender_from_settings(plain, sessions), VkSender)
