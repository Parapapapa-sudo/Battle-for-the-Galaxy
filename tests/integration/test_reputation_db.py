"""Репутация у фракций: стадии, источники (убийства/события), вступление, виды фракций."""
import random
import uuid

import pytest
import sqlalchemy as sa

from app import battles, characters, reputation
from app.models_game import Character, Faction, Reputation, Ship
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(session, seeded, faction: str = "azimuth") -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    return await characters.create_character(
        session, account_id=account.id, name=f"Реп{uuid.uuid4().hex[:6]}", faction_code=faction
    )


async def _rep(session, character, faction_code: str) -> int | None:
    faction = await session.scalar(sa.select(Faction).where(Faction.code == faction_code))
    assert faction is not None
    row = await session.get(Reputation, (character.id, faction.id))
    return row.value if row else None


@requires_db
async def test_stages_boundaries(session, seeded):
    """Границы стадий: ненависть < -10000 ≤ враждебность < 0 ≤ нейтралитет < 10000 ≤
    дружелюбие < 50000 ≤ превознесение."""
    cases = [
        (-10001, "Ненависть"),
        (-10000, "Враждебность"),
        (-1, "Враждебность"),
        (0, "Нейтралитет"),
        (9999, "Нейтралитет"),
        (10000, "Дружелюбие"),
        (49999, "Дружелюбие"),
        (50000, "Превознесение"),
    ]
    for value, expected in cases:
        assert await reputation.stage_label(session, value) == expected, value


@requires_db
async def test_join_sets_own_and_penalizes_other_main_factions(session, seeded):
    """Вступление: своя фракция — start_value, остальные MAIN — штраф, нейтральных нет."""
    character = await _character(session, seeded, faction="helion")
    assert await _rep(session, character, "helion") == 0
    for other in ("azimuth", "cinder", "lumen"):
        assert await _rep(session, character, other) == -1000  # faction.join_penalty
    assert await _rep(session, character, "pirates") is None  # нейтральные — с нуля


@requires_db
async def test_neutral_faction_not_joinable(session, seeded):
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    with pytest.raises(DomainError) as exc_info:
        await characters.create_character(
            session, account_id=account.id, name=f"Пират{uuid.uuid4().hex[:5]}",
            faction_code="pirates",
        )
    assert exc_info.value.code == "faction_not_joinable"


@requires_db
async def test_enemy_kill_grants_reputation(session, seeded):
    """Победа над пиратом снижает репутацию с Пиратами (enemy.reputation)."""
    character = await _character(session, seeded)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.base_max_hull = 2000
    ship.current_hull = 2000
    await session.flush()
    battle = await battles.start_battle(session, character, enemy_code="pirate_scout")
    rng = random.Random(5)
    for _ in range(60):
        battle = await battles.player_attack(session, character, rng=rng)
        if battle.status != "active":
            break
    assert battle.status == "won"
    assert await _rep(session, character, "pirates") == -25  # scout: -25


@requires_db
async def test_event_outcome_reputation(session, seeded):
    """Исход события может давать/отнимать репутацию (outcome.reputation)."""
    character = await _character(session, seeded)
    result = await reputation.grant(session, character, "veil_keepers", 150)
    assert result is not None
    name, value = result
    assert name == "Хранители завесы" and value == 150
    lines = await reputation.apply_deltas(session, character, {"red_syndicate": -300})
    assert lines and "Красный синдикат" in lines[0]
    assert await _rep(session, character, "red_syndicate") == -300
    # стадия в строке профиля
    block = await reputation.describe_reputations(session, character)
    assert "Хранители завесы" in block and "Нейтралитет" in block
    assert "Враждебность" in block  # red_syndicate -300
