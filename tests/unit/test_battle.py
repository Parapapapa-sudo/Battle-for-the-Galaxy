"""Чистая логика боя: урон, ИИ противника, отступление, лут, метки шансов."""
import random

from core.battle import (
    EnemyLoadout,
    apply_incoming_modifiers,
    chance_label,
    enemy_choose_action,
    roll_damage,
    roll_loot,
    roll_retreat,
)

LOADOUT = EnemyLoadout(
    code="test", name="Тестовый", category="normal", hull=50, damage=10, xp=5
)
ELITE = EnemyLoadout(
    code="elite", name="Элитный", category="elite", hull=100, damage=15, xp=20,
    passives={"damage_reduction_pct": 20}, weaknesses={"bonus_damage_taken_pct": 25},
)


def test_damage_roll_bounds():
    rng = random.Random(1)
    for _ in range(200):
        dmg = roll_damage(10, rng, spread_pct=10)
        assert 9 <= dmg <= 11


def test_damage_min_one():
    assert roll_damage(1, random.Random(1), spread_pct=10) >= 1


def test_passives_and_weaknesses():
    # 100 * 0.8 (пассив) = 80; 80 * 1.25 (слабость) = 100
    assert apply_incoming_modifiers(100, ELITE.passives, ELITE.weaknesses) == 100
    # только пассив: 100 -> 80
    assert apply_incoming_modifiers(100, ELITE.passives, {}) == 80


def test_enemy_ai_uses_ability_when_ready():
    loadout = EnemyLoadout(
        code="x", name="X", category="normal", hull=10, damage=5, xp=1,
        abilities=[{"code": "burst", "name": "B", "damage_mult": 2.0, "cooldown": 3, "chance": 1.0}],
    )
    action = enemy_choose_action(loadout, {}, turn_no=1, rng=random.Random(1))
    assert action["kind"] == "ability"
    # на кулдауне — базовая атака
    action = enemy_choose_action(loadout, {"burst": 4}, turn_no=2, rng=random.Random(1))
    assert action["kind"] == "attack"


def test_retreat_rules():
    # гарантированный успех без последствий
    ok = roll_retreat(
        chance_pct=100, damage_chance_pct=0, damage_amount=10,
        malfunction_chance_pct=0, rng=random.Random(1),
    )
    assert ok.success and ok.damage == 0 and not ok.malfunction
    # гарантированная неудача
    fail = roll_retreat(
        chance_pct=0, damage_chance_pct=100, damage_amount=10,
        malfunction_chance_pct=100, rng=random.Random(1),
    )
    assert not fail.success
    # успех с повреждением и поломкой
    hurt = roll_retreat(
        chance_pct=100, damage_chance_pct=100, damage_amount=10,
        malfunction_chance_pct=100, rng=random.Random(1),
    )
    assert hurt.success and hurt.damage == 10 and hurt.malfunction


def test_roll_loot_chance_zero_gives_nothing():
    entries = [{"code": "a", "weight": 10, "qty_min": 1, "qty_max": 2, "chance": 0.0}]
    assert roll_loot(entries, random.Random(1)) == []


def test_roll_loet_qty_within_bounds():
    entries = [{"code": "a", "weight": 10, "qty_min": 2, "qty_max": 4, "chance": 1.0}]
    for s in range(20):
        loot = roll_loot(entries, random.Random(s))
        assert loot and 2 <= loot[0]["qty"] <= 4


def test_chance_labels():
    assert chance_label(20) == "низкая"
    assert chance_label(50) == "средняя"
    assert chance_label(80) == "высокая"
