"""Чистая логика укладки предметов в стеки (инвентарь без системы веса — ТЗ).

Стек описывается занятой ячейкой: (qty, stack_limit). merge_into_stacks возвращает
новое состояние стеков и остаток, который не поместился — вызывающий код решает,
что с ним делать (прямой дроп ТЗ теряет, результат таймера уходит в «Ожидает получения»).
"""
from dataclasses import dataclass


@dataclass
class Stack:
    qty: int
    limit: int  # stack_limit предмета

    @property
    def is_full(self) -> bool:
        return self.qty >= self.limit


def merge_into_stacks(
    stacks: list[Stack],
    qty: int,
    *,
    limit: int,
    free_slots: int,
) -> tuple[list[Stack], int]:
    """Докладывает qty предметов (лимит стека limit) в существующие стеки,
    затем в новые ячейки, пока есть free_slots. Возвращает (новые стеки, остаток)."""
    if qty < 0:
        raise ValueError("qty must be >= 0")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if free_slots < 0:
        raise ValueError("free_slots must be >= 0")

    result = [Stack(s.qty, s.limit) for s in stacks]
    remaining = qty

    for stack in result:
        if remaining == 0:
            break
        if not stack.is_full and stack.limit == limit:
            take = min(remaining, limit - stack.qty)
            stack.qty += take
            remaining -= take

    while remaining > 0 and free_slots > 0:
        take = min(remaining, limit)
        result.append(Stack(take, limit))
        remaining -= take
        free_slots -= 1

    return result, remaining


def fits(stacks: list[Stack], qty: int, *, limit: int, free_slots: int) -> bool:
    """Поместится ли qty предметов без остатка?"""
    _, leftover = merge_into_stacks(stacks, qty, limit=limit, free_slots=free_slots)
    return leftover == 0
