"""Чистая логика развития: уровни модулей по порогам опыта, значения эффектов,
стоимость сброса сборки (формула повторных сбросов настраивается — ТЗ)."""
from collections.abc import Sequence


def level_for_xp(xp: int, thresholds: Sequence[int]) -> int:
    """Уровень = число порогов, достигнутых xp (пороги по возрастанию, [0]=1 уровень)."""
    if not thresholds:
        return 1
    level = 0
    for threshold in thresholds:
        if xp >= threshold:
            level += 1
        else:
            break
    return max(1, level)


def effect_value(base: int, per_level: int, level: int) -> int:
    """Значение эффекта на уровне: base + per_level × (level−1)."""
    return base + per_level * max(0, level - 1)


def reset_cost(base_qty: int, resets_done: int, *, formula: str = "linear") -> int:
    """Стоимость полного сброса распределения опыта.

    linear: base × (resets_done + 1). Общий опыт (xp_pool) не тратится (ТЗ) —
    оплачивается спец. предмет/ресурс.
    """
    if formula != "linear":
        raise ValueError(f"unknown reset formula: {formula}")
    return base_qty * (resets_done + 1)
