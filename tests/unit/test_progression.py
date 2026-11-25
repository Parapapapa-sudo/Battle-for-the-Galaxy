"""Развитие: уровни по порогам, эффекты, стоимость сброса."""
import pytest

from core.progression import effect_value, level_for_xp, reset_cost


def test_level_for_xp_thresholds():
    thresholds = [0, 50, 150, 300, 500]
    assert level_for_xp(0, thresholds) == 1
    assert level_for_xp(49, thresholds) == 1
    assert level_for_xp(50, thresholds) == 2
    assert level_for_xp(149, thresholds) == 2
    assert level_for_xp(150, thresholds) == 3
    assert level_for_xp(10000, thresholds) == 5  # больше порогов нет


def test_level_for_xp_empty_thresholds():
    assert level_for_xp(100, []) == 1


def test_effect_value_scales_per_level():
    assert effect_value(2, 1, 1) == 2
    assert effect_value(2, 1, 3) == 4
    assert effect_value(1, 2, 4) == 7


def test_reset_cost_linear_growth():
    assert reset_cost(5, 0) == 5
    assert reset_cost(5, 1) == 10
    assert reset_cost(5, 2) == 15


def test_reset_cost_unknown_formula_raises():
    with pytest.raises(ValueError, match="unknown reset formula"):
        reset_cost(5, 0, formula="exponential")


def test_level_for_xp_stops_at_first_unmet_threshold_even_if_unsorted():
    # пороги не по возрастанию: считаем до первого невыполненного (break)
    thresholds = [100, 0, 50]
    assert level_for_xp(60, thresholds) == 1  # 100 не достигнут — остальные не проверяются
    assert level_for_xp(150, thresholds) == 3  # все три порога достигнуты
