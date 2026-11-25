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


def test_damage_exact_when_spread_zero():
    for seed in range(50):
        assert roll_damage(37, random.Random(seed), spread_pct=0) == 37


def test_damage_bounds_ninety_to_one_hundred_ten():
    for seed in range(20):
        rng = random.Random(seed)
        for _ in range(200):
            dmg = roll_damage(100, rng, spread_pct=10)
            assert 90 <= dmg <= 110


def test_incoming_modifiers_combo_uses_floor_rounding():
    # 99 * 0.8 = 79.2 -> 79 (отбрасывание дробной части); 79 * 1.25 = 98.75 -> 98
    assert apply_incoming_modifiers(99, {"damage_reduction_pct": 20}, {"bonus_damage_taken_pct": 25}) == 98


def test_incoming_modifiers_never_below_one():
    assert apply_incoming_modifiers(5, {"damage_reduction_pct": 99}, {}) == 1


def test_retreat_failure_never_applies_damage_or_malfunction():
    fail = roll_retreat(
        chance_pct=0, damage_chance_pct=100, damage_amount=10,
        malfunction_chance_pct=100, rng=random.Random(1),
    )
    assert not fail.success
    assert fail.damage == 0
    assert not fail.malfunction


def test_roll_loot_respects_weights_statistically():
    entries = [
        {"code": "a", "weight": 90, "qty_min": 1, "qty_max": 1, "chance": 1.0},
        {"code": "b", "weight": 10, "qty_min": 1, "qty_max": 1, "chance": 1.0},
    ]
    counts = {"a": 0, "b": 0}
    for seed in range(500):
        loot = roll_loot(entries, random.Random(seed))
        assert len(loot) == 1
        counts[loot[0]["code"]] += 1
    # ожидание: a ≈ 450, b ≈ 50; допуск ±15%
    assert 380 <= counts["a"] <= 480
    assert 20 <= counts["b"] <= 120


def test_chance_label_threshold_boundaries():
    # границы включены в соседние метки
    assert chance_label(35) == "низкая"
    assert chance_label(36) == "средняя"
    assert chance_label(64) == "средняя"
    assert chance_label(65) == "высокая"


def test_chance_label_custom_thresholds():
    assert chance_label(50, low_max=60, high_min=90) == "низкая"
    assert chance_label(80, low_max=60, high_min=90) == "средняя"
