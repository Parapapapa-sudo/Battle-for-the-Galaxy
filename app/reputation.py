"""Репутация игрока у фракций: стадии, начисление, списки.

Стадии (пороги — config_params):
- ниже reputation.hatred_below (-10000) — Ненависть
- до 0 — Враждебность
- до reputation.friendly_from (10000) — Нейтралитет
- до reputation.exalted_from (50000) — Дружелюбие
- выше — Превознесение
Источники изменений: убийства врагов (enemy_templates.reputation),
исходы событий (outcome.reputation), вступление во фракцию (штраф остальным main).
"""
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import stores
from app.locale import t
from app.models_game import Character, Faction, Reputation


async def stage_label(session: AsyncSession, value: int, lang: str = "ru") -> str:
    """Стадия отношений по значению репутации (пороги настраиваются конфигом)."""
    hatred_below = int(await stores.get_param(session, "reputation.hatred_below", -10000))
    friendly_from = int(await stores.get_param(session, "reputation.friendly_from", 10000))
    exalted_from = int(await stores.get_param(session, "reputation.exalted_from", 50000))
    if value < hatred_below:
        return t("rep_hatred", lang)
    if value < 0:
        return t("rep_hostile", lang)
    if value < friendly_from:
        return t("rep_neutral", lang)
    if value < exalted_from:
        return t("rep_friendly", lang)
    return t("rep_exalted", lang)


async def grant(
    session: AsyncSession,
    character: Character,
    faction_code: str,
    delta: int,
) -> tuple[str, int] | None:
    """Изменить репутацию с фракцией (строка создаётся при первом изменении).

    Возвращает (название фракции, новое значение) или None — фракции нет.
    """
    if delta == 0:
        return None
    faction = await session.scalar(sa.select(Faction).where(Faction.code == faction_code))
    if faction is None:
        return None
    row = await session.get(Reputation, (character.id, faction.id))
    if row is None:
        row = Reputation(character_id=character.id, faction_id=faction.id, value=0, is_active=True)
        session.add(row)
    row.value += delta
    await session.flush()
    return faction.name, row.value


async def apply_deltas(
    session: AsyncSession,
    character: Character,
    deltas: dict[str, Any],
    lang: str = "ru",
) -> list[str]:
    """Применить пачку изменений {код_фракции: дельта}; вернуть строки для сообщения."""
    lines: list[str] = []
    for code, delta in deltas.items():
        result = await grant(session, character, str(code), int(delta))
        if result is None:
            continue
        name, value = result
        lines.append(
            t("effect_rep", lang, delta=int(delta), faction=name)
            + " "
            + t("rep_stage_suffix", lang, stage=await stage_label(session, value, lang))
        )
    return lines


async def list_reputations(
    session: AsyncSession, character: Character
) -> list[dict[str, Any]]:
    """Все репутации игрока: фракция, вид, значение, стадия (по убыванию значения)."""
    rows = (
        await session.execute(
            sa.select(Faction.name, Faction.kind, Reputation.value)
            .join(Reputation, Reputation.faction_id == Faction.id)
            .where(Reputation.character_id == character.id)
            .order_by(Reputation.value.desc())
        )
    ).all()
    return [
        {
            "faction": name,
            "kind": kind,
            "value": value,
            "stage": await stage_label(session, value, character.lang),
        }
        for name, kind, value in rows
    ]


async def describe_reputations(session: AsyncSession, character: Character) -> str:
    """Блок репутации для профиля: «Фракция: +N (стадия)» по строкам."""
    rows = await list_reputations(session, character)
    if not rows:
        return ""
    return "\n".join(
        t("rep_line", character.lang, faction=r["faction"], value=r["value"], stage=r["stage"])
        for r in rows
    )


async def setup_faction_reputations(
    session: AsyncSession, character: Character, faction: Faction
) -> None:
    """Стартовая репутация при вступлении: своя — start_value, остальные MAIN — штраф.

    Нейтральные фракции стартуют с нуля (строки не создаём). Повторное вступление
    (смена фракции) не затирает заработанное: штраф применяется только к новым строкам.
    """
    start = int(await stores.get_param(session, "reputation.start_value", 0))
    penalty = int(await stores.get_param(session, "faction.join_penalty", -1000))
    session.add(
        Reputation(
            character_id=character.id, faction_id=faction.id, value=start, is_active=True
        )
    )
    other_mains = (
        await session.scalars(
            sa.select(Faction).where(Faction.kind == "main", Faction.id != faction.id)
        )
    ).all()
    for other in other_mains:
        session.add(
            Reputation(
                character_id=character.id, faction_id=other.id, value=penalty, is_active=True
            )
        )
    await session.flush()
