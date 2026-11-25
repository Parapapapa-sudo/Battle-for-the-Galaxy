"""Укладка в стеки: дочерновка неполных стеков, новые ячейки, остаток."""
import pytest

from core.inventory import Stack, fits, merge_into_stacks


def test_empty_inventory_new_stacks():
    stacks, leftover = merge_into_stacks([], 60, limit=50, free_slots=5)
    assert [(s.qty, s.limit) for s in stacks] == [(50, 50), (10, 50)]
    assert leftover == 0


def test_top_up_partial_stack_first():
    stacks, leftover = merge_into_stacks([Stack(40, 50)], 20, limit=50, free_slots=1)
    assert [(s.qty, s.limit) for s in stacks] == [(50, 50), (10, 50)]
    assert leftover == 0


def test_leftover_when_no_slots():
    stacks, leftover = merge_into_stacks([Stack(50, 50)], 10, limit=50, free_slots=0)
    assert len(stacks) == 1
    assert leftover == 10


def test_unique_stack_limit_one():
    stacks, leftover = merge_into_stacks([], 3, limit=1, free_slots=30)
    assert len(stacks) == 3  # каждая единица — своя ячейка
    assert all(s.qty == 1 for s in stacks)
    assert leftover == 0


def test_fits():
    assert fits([Stack(30, 50)], 20, limit=50, free_slots=0)
    assert not fits([Stack(30, 50)], 21, limit=50, free_slots=0)
    assert fits([], 30, limit=50, free_slots=1)


def test_invalid_args():
    with pytest.raises(ValueError):
        merge_into_stacks([], -1, limit=50, free_slots=1)
    with pytest.raises(ValueError):
        merge_into_stacks([], 1, limit=0, free_slots=1)
    with pytest.raises(ValueError):
        merge_into_stacks([], 1, limit=50, free_slots=-1)
