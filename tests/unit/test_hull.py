import pytest

from core.hull import max_hull


def test_no_malfunctions() -> None:
    assert max_hull(1000, 0) == 1000


def test_each_malfunction_minus_5_pct() -> None:
    # ТЗ: каждая поломка снижает максимум на 5%
    assert max_hull(1000, 1) == 950
    assert max_hull(1000, 2) == 900
    assert max_hull(1000, 4) == 800


def test_never_below_zero() -> None:
    assert max_hull(100, 25) == 0
    assert max_hull(100, 100) == 0


def test_invalid_args() -> None:
    with pytest.raises(ValueError):
        max_hull(-1, 0)
    with pytest.raises(ValueError):
        max_hull(100, -1)
    with pytest.raises(ValueError):
        max_hull(100, 1, penalty_pct=101)
