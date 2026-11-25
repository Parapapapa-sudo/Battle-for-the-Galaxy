"""Чистые функции прочности корабля (ТЗ: каждая поломка снижает максимум на 5%)."""


def max_hull(base_max: int, malfunctions: int, *, penalty_pct: int = 5) -> int:
    """Максимальная прочность = base_max × (1 − n × penalty_pct / 100).

    Неотрицательный результат; поломки сверх лимита не уводят прочность ниже нуля.
    """
    if base_max < 0:
        raise ValueError("base_max must be >= 0")
    if malfunctions < 0:
        raise ValueError("malfunctions must be >= 0")
    if not 0 <= penalty_pct <= 100:
        raise ValueError("penalty_pct must be in [0, 100]")
    return base_max * max(0, 100 - malfunctions * penalty_pct) // 100
