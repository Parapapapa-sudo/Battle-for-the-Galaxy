"""Чистая логика экспедиций и событий (без БД).

- roll_queue: взвешенная генерация последовательности событий; уникальные
  шаблоны — не более одного раза за экспедицию (ТЗ);
- pick_variant: выбор исхода по chance (последний вариант — без chance = остаток);
- danger_label: приблизительная оценка опасности для игрока (ТЗ: низкая/средняя/высокая).
"""
import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueueEntry:
    code: str
    weight: int
    is_unique: bool = False


def roll_queue(
    entries: list[QueueEntry],
    length: int,
    rng: random.Random | None = None,
    *,
    used_unique: set[str] | None = None,
) -> list[str]:
    """Взвешенный выбор с повторами; уникальные шаблоны выпадают <= 1 раза.

    used_unique — уже использованные уникальные коды (продление очереди
    экспедиции: правило «≤1 за экспедицию» держится между продлениями).
    """
    rng = rng or random.Random()
    if not entries or length <= 0:
        return []
    result: list[str] = []
    used: set[str] = set(used_unique or set())
    for _ in range(length):
        candidates = [e for e in entries if not (e.is_unique and e.code in used)]
        if not candidates:
            break
        total = sum(max(0, e.weight) for e in candidates)
        if total <= 0:
            break
        roll = rng.uniform(0, total)
        acc = 0.0
        for entry in candidates:
            acc += max(0, entry.weight)
            if roll <= acc:
                result.append(entry.code)
                if entry.is_unique:
                    used.add(entry.code)
                break
    return result


def pick_variant(variants: list[dict[str, Any]], rng: random.Random | None = None) -> dict[str, Any]:
    """Первый вариант, чей накопленный chance перекрывает бросок; последний — fallback."""
    if not variants:
        raise ValueError("variants must be non-empty")
    rng = rng or random.Random()
    roll = rng.random()
    acc = 0.0
    for variant in variants[:-1]:
        acc += float(variant.get("chance", 0.0))
        if roll < acc:
            return variant
    return variants[-1]


def danger_label(
    danger_min: int, danger_max: int, *, low_max: int = 3, high_min: int = 7
) -> str:
    """Средняя опасность → приблизительная метка (ТЗ: точная скрыта от игрока)."""
    avg = (danger_min + danger_max) / 2
    if avg <= low_max:
        return "низкая"
    if avg >= high_min:
        return "высокая"
    return "средняя"
