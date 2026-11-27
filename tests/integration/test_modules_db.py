"""Этап 5: модули и развитие — установка/снятие, опыт, пересчёт, сброс, боевые способности."""
import random
import uuid

import pytest

from app import battles, characters, expeditions, modules
from app.models_game import Character, Ship
from core.errors import DomainError
from tests.conftest import requires_db


async def _character(
    session, seeded, faction: str = "azimuth", fuel: int = 50
) -> Character:
    account, _ = await characters.get_or_create_account(
        session, platform="console", platform_user_id=f"user-{uuid.uuid4().hex[:12]}"
    )
    character = await characters.create_character(
        session, account_id=account.id, name=f"Тестер{uuid.uuid4().hex[:6]}", faction_code=faction
    )
    character.hall = "workshop"  # снятие модулей — только в Мастерской
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = fuel
    await session.flush()
    return character


@requires_db
async def test_faction_starting_module_granted(session, seeded):
    character = await _character(session, seeded, faction="azimuth")
    mods = await modules.list_modules(session, character)
    assert len(mods) == 1
    assert mods[0]["code"] == "aim_computer"
    assert not mods[0]["installed"]  # получен, но не установлен


@requires_db
async def test_install_uninstall_keeps_progress_and_enforces_max(session, seeded):
    character = await _character(session, seeded)
    character.xp_pool = 100
    await modules.grant_module(session, character, "aim_computer")  # второй экземпляр
    await modules.install_module(session, character, "aim_computer")
    mods = await modules.list_modules(session, character)
    assert sum(1 for m in mods if m["installed"]) == 1

    await modules.train_module(session, character, "aim_computer", 60)  # уровень 2
    await modules.uninstall_module(session, character, "aim_computer")
    mods = await modules.list_modules(session, character)
    installed = [m for m in mods if m["code"] == "aim_computer" and m["installed"]]
    assert not installed
    assert any(m["xp_invested"] == 60 for m in mods)  # прогресс сохранён (ТЗ)

    # max_per_ship=2: третья установка упирается в лимит
    await modules.install_module(session, character, "aim_computer")
    await modules.install_module(session, character, "aim_computer")
    await modules.grant_module(session, character, "aim_computer")  # третий экземпляр
    with pytest.raises(DomainError) as exc_info:
        await modules.install_module(session, character, "aim_computer")
    assert exc_info.value.code == "max_per_ship"


@requires_db
async def test_install_requires_port(session, seeded):
    character = await _character(session, seeded)
    await expeditions.launch_expedition(session, character)
    with pytest.raises(DomainError) as exc_info:
        await modules.install_module(session, character, "aim_computer")
    assert exc_info.value.code == "not_in_port"


@requires_db
async def test_train_moves_xp_and_requires_port(session, seeded):
    character = await _character(session, seeded)
    character.xp_pool = 100
    await modules.train_module(session, character, "aim_computer", 40)
    assert character.xp_pool == 60
    mods = await modules.list_modules(session, character)
    assert mods[0]["xp_invested"] == 40
    assert mods[0]["level"] == 1

    with pytest.raises(DomainError) as exc_info:
        await modules.train_module(session, character, "aim_computer", 1000)
    assert exc_info.value.code == "not_enough_xp"

    # в экспедции тренироваться нельзя (ТЗ: распределение опыта — только в космопорту)
    ship = await session.get(Ship, character.id)
    assert ship is not None
    ship.fuel = 50
    await expeditions.launch_expedition(session, character)
    with pytest.raises(DomainError) as exc_info:
        await modules.train_module(session, character, "aim_computer", 10)
    assert exc_info.value.code == "not_in_port"


@requires_db
async def test_effective_attack_includes_installed_skills(session, seeded):
    character = await _character(session, seeded)
    character.xp_pool = 50
    base = await modules.effective_attack(session, character)
    assert base == 5  # атака стартового корабля

    await modules.install_module(session, character, "aim_computer")
    await modules.train_module(session, character, "aim_computer", 50)  # уровень 2: +2+1
    attack = await modules.effective_attack(session, character)
    assert attack == base + 3  # пересчёт характеристик (ТЗ)

    # снятый модуль не даёт бонус, но прогресс остаётся
    await modules.uninstall_module(session, character, "aim_computer")
    assert await modules.effective_attack(session, character) == base


@requires_db
async def test_reset_build_returns_xp_and_scales_cost(session, seeded):
    character = await _character(session, seeded)
    character.xp_pool = 200
    await characters.grant_items(session, character, code="trinium_ore", qty=20, allow_loss=True)
    await modules.install_module(session, character, "aim_computer")
    await modules.train_module(session, character, "aim_computer", 120)
    assert character.xp_pool == 80

    returned = await modules.reset_build(session, character)
    assert returned == 120
    assert character.xp_pool == 200  # общий опыт не потерян (ТЗ)
    assert character.build_resets == 1
    mods = await modules.list_modules(session, character)
    assert mods[0]["xp_invested"] == 0
    assert mods[0]["installed"]  # модули остались установленными

    # второй сброс: linear — 10× триния (осталось 15 — хватает)
    await modules.train_module(session, character, "aim_computer", 10)
    await modules.reset_build(session, character)
    assert character.build_resets == 2
    assert character.xp_pool == 200

    # третий сброс: 15× триния — осталось 5, ошибка
    await modules.train_module(session, character, "aim_computer", 10)
    with pytest.raises(DomainError) as exc_info:
        await modules.reset_build(session, character)
    assert exc_info.value.code == "not_enough_resources"


@requires_db
async def test_ability_module_in_battle_with_cooldown(session, seeded):
    character = await _character(session, seeded, faction="helion")  # overclock_core: shot ×2.0
    await modules.install_module(session, character, "overclock_core")

    expedition = await expeditions.launch_expedition(session, character)
    battle = await battles.start_battle(
        session, character, enemy_code="pirate_scout", expedition=expedition
    )
    hull_before = int(battle.state["enemy_hull"])

    battle = await battles.player_ability(session, character, "shot", rng=random.Random(2))
    dealt = hull_before - int(battle.state["enemy_hull"])
    assert dealt >= 8  # атака 5±10% ×2.0 ≈ 9-11 (с разбросом)

    # кулдаун 3: повторный shot в ближайшие ходы запрещён
    with pytest.raises(DomainError) as exc_info:
        await battles.player_ability(session, character, "shot", rng=random.Random(2))
    assert exc_info.value.code == "on_cooldown"

    # обычная атака продолжает бой; после 3 ходов способность снова доступна
    for _ in range(3):
        battle = await battles.player_attack(session, character, rng=random.Random(5))
        if battle.status != "active":
            break
    if battle.status == "active":
        battle = await battles.player_ability(session, character, "shot", rng=random.Random(2))
        assert battle.status in ("active", "won")


@requires_db
async def test_unknown_ability_command_rejected(session, seeded):
    character = await _character(session, seeded)
    expedition = await expeditions.launch_expedition(session, character)
    await battles.start_battle(
        session, character, enemy_code="pirate_scout", expedition=expedition
    )
    with pytest.raises(DomainError) as exc_info:
        await battles.player_ability(session, character, "shot", rng=random.Random(1))
    assert exc_info.value.code == "no_ability"
