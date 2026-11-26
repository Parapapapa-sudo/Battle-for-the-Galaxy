"""Картинки-паки: выбор из папки, кэш attachment, привязка паков к командам."""
import random
import uuid
from pathlib import Path

from app import characters, expeditions, images
from app.router import route_text_command
from tests.conftest import requires_db


def _make_pack(base: Path, pack: str, names: list[str]) -> Path:
    folder = base / pack
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"\xff\xd8\xff")  # неважно, содержимое не читается
    return folder


def test_pick_random_from_pack(tmp_path: Path):
    _make_pack(tmp_path, "travel", ["a.jpg", "b.png", "c.txt"])  # .txt не картинка
    files = images.list_pack_files("travel", tmp_path)
    assert [f.name for f in files] == ["a.jpg", "b.png"]
    picked = images.pick_random("travel", tmp_path, rng=random.Random(1))
    assert picked is not None and picked.suffix in (".jpg", ".png")
    # пустой/несуществующий пак — None (сообщение уйдёт без фото)
    assert images.pick_random("no_such_pack", tmp_path) is None


@requires_db
async def test_battle_outcome_carries_image_pack(session, seeded, sessions):
    """Финал боя: победа → пак victory, раунды — без картинки."""
    from app.bus import CommandBus
    from app.models_game import Ship

    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"

    def send(text: str):
        return route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text=text, idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
        )

    await send("start Боец Тестов azimuth")
    await send("launch")
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    character = await characters.get_active_character(session, account.id)
    assert character is not None
    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    assert expedition is not None

    # буст корпуса — победа гарантирована
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.base_max_hull = 2000
    ship.current_hull = 2000
    await session.flush()
    from app import battles

    await battles.start_battle(
        session, character, enemy_code="pirate_scout", expedition=expedition
    )

    pack = None
    for _ in range(60):
        r = await send("attack")
        if r.image_pack is not None:
            pack = r.image_pack
            break
    assert pack == "victory"


@requires_db
async def test_attachment_cache_by_mtime(session, seeded, tmp_path: Path):
    folder = _make_pack(tmp_path, "travel", ["a.jpg"])
    path = folder / "a.jpg"
    assert await images.cached_attachment(session, path) is None
    await images.store_attachment(session, path, "photo1_2")
    assert await images.cached_attachment(session, path) == "photo1_2"
    # файл изменился (другой mtime) — кэш невалиден
    import os
    import time

    os.utime(path, (time.time() + 5, time.time() + 5))
    assert await images.cached_attachment(session, path) is None


@requires_db
async def test_commands_carry_image_packs(session, seeded, sessions):
    """Паки привязаны: go → travel, залы → port_*, прибытие → port_main (outbox)."""
    from app.bus import CommandBus

    bus = CommandBus()
    user = f"user-{uuid.uuid4().hex[:8]}"

    def send(text: str):
        return route_text_command(
            bus, sessions, platform="console", platform_user_id=user,
            text=text, idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
        )

    await send("start Картинкин Тест azimuth")
    r = await send("hall workshop")
    assert r.image_pack == "port_workshop"
    r = await send("hall main")
    assert r.image_pack == "port_main"

    r = await send("launch")
    assert r.image_pack == "travel"
    r = await send("go")
    assert r.image_pack == "travel"  # экран пути

    # прибытие: outbox-уведомление несёт payload с паком port_main
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa

    from app.models import Outbox, Timer

    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=user
    )
    character = await characters.get_active_character(session, account.id)
    assert character is not None
    expedition = await expeditions.get_ongoing_expedition(session, character.id)
    assert expedition is not None
    # снимаем «в пути» (go выше отправил в путешествие) — иначе return заблокирован
    expedition.next_event_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await expeditions.request_return(session, character)
    expedition.return_eta = datetime.now(UTC) - timedelta(seconds=1)
    await session.execute(
        sa.update(Timer)
        .where(Timer.type == "exp_return_arrival", Timer.status == "pending")
        .values(fires_at=expedition.return_eta)
    )
    await session.flush()
    await expeditions.arrive_expedition(session, {"expedition_id": str(expedition.id)})
    rows = (
        await session.scalars(
            sa.select(Outbox).where(
                Outbox.character_id == character.id, Outbox.text.like("%Прибыли%")
            )
        )
    ).all()
    assert rows and rows[-1].payload == {"image_pack": "port_main"}
