"""Картинки к сообщениям: паки в assets/images/<пак>/, случайный выбор, кэш загрузки.

Пак = папка с файлами (jpg/jpeg/png/gif); пополняется простым копированием файлов.
Загруженное в VK фото переиспользуется: attachment кэшируется в config_params
(`images.cache`, ключ — путь файла, инвалидация по mtime), повторная загрузка
не нужна.
"""
import random
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.models import ConfigParam

BASE_DIR = Path("assets/images")
EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
CACHE_KEY = "images.cache"


def list_pack_files(pack: str, base_dir: Path | None = None) -> list[Path]:
    folder = (base_dir or BASE_DIR) / pack
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXTENSIONS)


def pick_random(
    pack: str, base_dir: Path | None = None, rng: random.Random | None = None
) -> Path | None:
    """Случайная картинка пака; None — пак пуст/не существует (сообщение без фото)."""
    files = list_pack_files(pack, base_dir)
    if not files:
        return None
    return (rng or random).choice(files)


def _signature(path: Path) -> str:
    return f"{path.as_posix()}:{path.stat().st_mtime_ns}"


async def cached_attachment(session: AsyncSession, path: Path) -> str | None:
    cache: dict[str, Any] = await stores.get_param(session, CACHE_KEY, {}) or {}
    entry = cache.get(path.as_posix())
    if entry and entry.get("sig") == _signature(path):
        return str(entry["attachment"])
    return None


async def store_attachment(session: AsyncSession, path: Path, attachment: str) -> None:
    cache: dict[str, Any] = dict(await stores.get_param(session, CACHE_KEY, {}) or {})
    cache[path.as_posix()] = {"sig": _signature(path), "attachment": attachment}
    param = await session.get(ConfigParam, CACHE_KEY)
    if param is None:
        session.add(ConfigParam(key=CACHE_KEY, value=cache, updated_by="images"))
    else:
        param.value = cache
    await session.flush()
