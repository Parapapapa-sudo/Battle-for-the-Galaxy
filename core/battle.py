"""Чистая логика пошагового боя (без БД).

ТЗ-критерии:
- базовый порядок: игрок, затем противник; без автоматического хода при бездействии игрока;
- простая логика противников; активные способности по cooldown/chance;
- пассивы и слабости применяются только у elite/boss (контент-ограничение);
- одна попытка отступления; при успехе противник исчезает, награды не выдаются;
- вероятности действий показываются приблизительно (низкая/средняя/высокая).
"""
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnemyLoadout:
    """Снапшот противника для боя (из enemy_templates + enemy_abilities)."""

    code: str
    name: str
    category: str
    hull: int
    damage: int
    xp: int
    abilities: list[dict[str, Any]] = field(default_factory=list)
    passives: dict[str, Any] = field(default_factory=dict)
    weaknesses: dict[str, Any] = field(default_factory=dict)

    @property
    def has_passives_or_weaknesses(self) -> bool:
        return bool(self.passives or self.weaknesses)


def roll_damage(base: int, rng: random.Random, *, spread_pct: int = 10) -> int:
    """Урон base ± spread%. Минимум 1."""
    low = base * (100 - spread_pct) // 100
    high = base * (100 + spread_pct) // 100
    return max(1, rng.randint(low, high))


def apply_incoming_modifiers(damage: int, passives: dict[str, Any], weaknesses: dict[str, Any]) -> int:
    """Пассивы противника снижают урон, слабости усиливают. Только elite/boss (контент)."""
    reduced = damage * (100 - int(passives.get("damage_reduction_pct", 0))) // 100
    boosted = reduced * (100 + int(weaknesses.get("bonus_damage_taken_pct", 0))) // 100
    return max(1, boosted)


def enemy_choose_action(
    loadout: EnemyLoadout, cooldowns: dict[str, int], turn_no: int, rng: random.Random
) -> dict[str, Any]:
    """Простая логика противника (ТЗ): способность по chance, если готова; иначе атака.

    cooldowns: код способности -> ход, когда снова доступна. Мутирует копию в state.
    """
    ready = [
        a
        for a in loadout.abilities
        if cooldowns.get(a["code"], 0) <= turn_no and rng.random() < float(a.get("chance", 0.0))
    ]
    if ready:
        ability = ready[0]
        return {
            "kind": "ability",
            "code": ability["code"],
            "name": ability["name"],
            "damage_mult": float(ability.get("damage_mult", 1.0)),
            "cooldown": int(ability.get("cooldown", 0)),
        }
    return {"kind": "attack", "damage_mult": 1.0}


def roll_loot(
    entries: list[dict[str, Any]], rng: random.Random
) -> list[dict[str, Any]]:
    """Таблица дропа: chance на выпадение, вес между кандидатами, qty в [min, max]."""
    available = [e for e in entries if rng.random() < float(e.get("chance", 1.0))]
    if not available:
        return []
    total = sum(max(0, int(e["weight"])) for e in available)
    if total <= 0:
        return []
    roll = rng.uniform(0, total)
    acc = 0.0
    for entry in available:
        acc += max(0, int(entry["weight"]))
        if roll <= acc:
            qty = rng.randint(int(entry["qty_min"]), int(entry["qty_max"]))
            return [{"code": entry["code"], "qty": qty}] if qty > 0 else []
    return []


@dataclass(frozen=True)
class RetreatResult:
    success: bool
    damage: int = 0
    malfunction: bool = False


def roll_retreat(
    *,
    chance_pct: int,
    damage_chance_pct: int,
    damage_amount: int,
    malfunction_chance_pct: int,
    rng: random.Random,
) -> RetreatResult:
    """Одна попытка отступления (ТЗ): успех = противник исчезает, наград нет;
    корабль может получить повреждение/поломку (вероятности настраиваются)."""
    if rng.randint(1, 100) > chance_pct:
        return RetreatResult(success=False)
    damage = damage_amount if rng.randint(1, 100) <= damage_chance_pct else 0
    malfunction = rng.randint(1, 100) <= malfunction_chance_pct
    return RetreatResult(success=True, damage=damage, malfunction=malfunction)


def chance_label(pct: int, *, low_max: int = 35, high_min: int = 65) -> str:
    """Приблизительная вероятность для отображения игроку (ТЗ)."""
    if pct <= low_max:
        return "низкая"
    if pct >= high_min:
        return "высокая"
    return "средняя"


def hp_bar(current: int, maximum: int, *, segments: int = 10) -> tuple[str, int]:
    """Графическая полоса здоровья: ■ заполненные, □ потерянные (1 квадрат = 10%).

    Возвращает (полоса, процент). Квадраты — округление вниз (75% → 7 ■),
    процент — математическое округление.
    """
    if maximum <= 0:
        return "□" * segments, 0
    pct = max(0, min(100, round(current * 100 / maximum)))
    # до ближайшего сегмента, половина вниз: 75% → 7 ■, 88% → 9 ■
    filled = min(segments, int(pct / (100 / segments) + 0.4999))
    return "■" * filled + "□" * (segments - filled), pct
