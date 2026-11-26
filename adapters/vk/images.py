"""Attachment для VK-сообщений: случайная картинка пака → загрузка (с кэшем)."""
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import images
from app.db import transactional

log = structlog.get_logger(__name__)


async def attachment_for(
    api: Any, sessions: async_sessionmaker[AsyncSession], pack: str, peer_id: int
) -> str | None:
    """Случайное фото пака как VK-attachment. None — пак пуст или сбой (не роняем ответ)."""
    path = images.pick_random(pack)
    if path is None:
        return None
    try:
        async with sessions() as session, transactional(session):
            cached = await images.cached_attachment(session, path)
        if cached:
            return cached
        attachment = await api.upload_message_photo(peer_id, path)
        async with sessions() as session, transactional(session):
            await images.store_attachment(session, path, attachment)
        return str(attachment)
    except Exception as exc:  # noqa: BLE001 — картинка опциональна, текст важнее
        log.warning("vk_image_failed", pack=pack, path=str(path), error=str(exc))
        return None
