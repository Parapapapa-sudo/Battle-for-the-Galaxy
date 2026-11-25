"""Нагрузочный тест команд (этап 10): RPS и доля «занято» при параллельных игроках.

Моделирует прод: N персонажей одновременно шлют команды через route_text_command
(полный путь: identity → account → bus → lock → handler). Замеряем:
  - исполненные команды/сек суммарно;
  - долю ответов «занят» (per-character последовательность, ТЗ);
  - ошибки.

Запуск:
  DATABASE_URL=... python -m tools.load_test --characters 20 --commands 10
"""
import argparse
import asyncio
import time
import uuid

from app import db
from app.bus import CommandBus
from app.characters import create_character, get_or_create_account
from app.router import route_text_command
from app.seeds import ensure_seeds


async def _prepare(characters_count: int) -> list[tuple[str, str]]:
    """Создаём персонажей (коммит) и возвращаем (user_id, name)."""
    users: list[tuple[str, str]] = []
    async with db.session_factory() as session, session.begin():
        await ensure_seeds(session)
        for _ in range(characters_count):
            user = f"load-{uuid.uuid4().hex[:10]}"
            account, _ = await get_or_create_account(
                session, platform="console", platform_user_id=user
            )
            name = f"Нагрузка{uuid.uuid4().hex[:6]}"
            await create_character(
                session, account_id=account.id, name=name, faction_code="azimuth"
            )
            users.append((user, name))
    return users


async def _player(user: str, commands: int, latency_ms: float) -> dict[str, int]:
    bus = CommandBus(db.session_factory)
    stats = {"ok": 0, "busy": 0, "error": 0}
    for _ in range(commands):
        key = f"load:{user}:{uuid.uuid4()}"
        try:
            result = await route_text_command(
                bus, db.session_factory,
                platform="console", platform_user_id=user,
                text="me", idempotency_key=key,
            )
            if result.data.get("error") == "busy":
                stats["busy"] += 1
            elif result.data.get("error"):
                stats["error"] += 1
            else:
                stats["ok"] += 1
        except Exception:  # noqa: BLE001 — нагрузочный тест считает любые сбои
            stats["error"] += 1
        await asyncio.sleep(latency_ms / 1000)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--characters", type=int, default=20)
    parser.add_argument("--commands", type=int, default=10)
    parser.add_argument("--latency-ms", type=float, default=50, help="пауза между командами игрока")
    args = parser.parse_args()

    print(f"Подготовка: {args.characters} персонажей...")
    users = await _prepare(args.characters)

    start = time.perf_counter()
    results = await asyncio.gather(
        *[_player(user, args.commands, args.latency_ms) for user, _ in users]
    )
    elapsed = time.perf_counter() - start

    total = args.characters * args.commands
    ok = sum(r["ok"] for r in results)
    busy = sum(r["busy"] for r in results)
    errors = sum(r["error"] for r in results)
    print(
        f"Итого: {total} команд за {elapsed:.1f}s → {total / elapsed:.0f} cmd/s | "
        f"ok={ok} busy={busy} ({100 * busy / total:.1f}%) error={errors}"
    )


if __name__ == "__main__":
    asyncio.run(main())
