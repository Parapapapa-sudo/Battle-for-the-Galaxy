"""Консольный адаптер (dev): тот же игровой роутер, что и VK, без платформы.

Формат строки: <пользователь> <текст команды>   (например: alice start Иван azimuth)
Идемпотентность: каждая строка — уникальный ключ (uuid); сценарии ретраев
платформы покрываются интеграционными тестами конвейера и VK-путём.
"""
import asyncio
import sys
import uuid
from io import TextIOWrapper

import structlog

from app import db
from app.bus import CommandBus
from app.config import get_settings
from app.logging import configure_logging
from app.router import route_text_command


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger("console")
    bus = CommandBus(db.session_factory)

    # Windows: stdin/stdout в пайпе по умолчанию в кодовой странице локали (cp1251),
    # а команды приходят в UTF-8 — иначе русские имена превращаются в суррогаты.
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, TextIOWrapper) and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")

    print("Консольный адаптер. Формат: <пользователь> <команда>. Пустая строка — выход.")
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line.strip():
                break
            parts = line.strip().split(maxsplit=1)
            if len(parts) < 2:
                print("Нужно: <пользователь> <команда>")
                continue
            user, text = parts[0], parts[1]
            try:
                result = await route_text_command(
                    bus,
                    db.session_factory,
                    platform="console",
                    platform_user_id=user,
                    text=text,
                    idempotency_key=f"console:{user}:{uuid.uuid4()}",
                )
                print(f"-> {result.message}")
            except Exception as exc:  # noqa: BLE001 — адаптер не падает от одной команды
                log.exception("command_crash")
                print(f"-> внутренняя ошибка: {exc}")
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
