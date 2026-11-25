"""Генерация очереди событий, выбор исхода, метки опасности."""
import random
from typing import Any

from core.expeditions import QueueEntry, danger_label, pick_variant, roll_queue

ENTRIES = [
    QueueEntry("common_a", 10),
    QueueEntry("common_b", 10),
    QueueEntry("unique_c", 10, is_unique=True),
]


def test_roll_queue_length_and_membership():
    queue = roll_queue(ENTRIES, 20, random.Random(42))
    assert len(queue) == 20
    assert set(queue) <= {"common_a", "common_b", "unique_c"}


def test_unique_event_appears_at_most_once():
    for seed in range(200):
        queue = roll_queue(ENTRIES, 30, random.Random(seed))
        assert queue.count("unique_c") <= 1, f"unique twice in seed {seed}"


def test_unique_not_repeated_across_queue_extensions():
    """Продление очереди: уникальные события ≤1 за экспедицию суммарно (ТЗ)."""
    for seed in range(200):
        rng = random.Random(seed)
        first = roll_queue(ENTRIES, 10, rng)
        used = {"unique_c"} if "unique_c" in first else set()
        second = roll_queue(ENTRIES, 10, rng, used_unique=used)
        combined = first + second
        assert combined.count("unique_c") <= 1, f"unique twice after extension, seed {seed}"


def test_roll_queue_empty_entries():
    assert roll_queue([], 5, random.Random(1)) == []


def test_weights_are_respected_approximately():
    entries = [QueueEntry("x", 90), QueueEntry("y", 10)]
    counts = {"x": 0, "y": 0}
    for seed in range(500):
        counts[roll_queue(entries, 1, random.Random(seed))[0]] += 1
    assert counts["x"] > 350  # ~90% с допуском


def test_pick_variant_deterministic_with_seed():
    variants: list[dict[str, Any]] = [{"chance": 0.5, "id": "a"}, {"id": "b"}]
    seen = {pick_variant(variants, random.Random(s)).get("id") for s in range(50)}
    assert seen == {"a", "b"}


def test_pick_variant_last_is_fallback():
    variants: list[dict[str, Any]] = [{"chance": 0.0, "id": "never"}, {"id": "fallback"}]
    for s in range(20):
        assert pick_variant(variants, random.Random(s))["id"] == "fallback"


def test_danger_labels():
    assert danger_label(1, 2) == "низкая"
    assert danger_label(4, 5, low_max=3, high_min=7) == "средняя"
    assert danger_label(7, 9) == "высокая"
