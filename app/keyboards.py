"""VK-клавиатуры: локализованные кнопки; payload несёт команду, label — текст на языке игрока.

- Элемент ряда: строка-команда (label из локали) или кортеж (команда, label).
- Событие с выбором: кнопки несут ТЕКСТ варианта, payload — «choice <key>»;
  отказ — «Пропустить», для боевых событий — «Проскочить».
"""
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.locale import t

COLOR_BY_COMMAND = {
    "attack": "positive",
    "go": "positive",
    "launch": "positive",
    "patch": "positive",
    "retreat": "negative",
    "decline": "negative",
}

LABEL_KEY = {
    "me": "kb_me",
    "inv": "kb_inv",
    "ship": "kb_ship",
    "quests": "kb_quests",
    "mods": "kb_mods",
    "wallet": "kb_wallet",
    "launch": "kb_launch",
    "shop": "kb_shop",
    "recipes": "kb_recipes",
    "go": "kb_go",
    "exp": "kb_exp",
    "return": "kb_return",
    "sync": "kb_sync",
    "patch": "kb_patch",
    "attack": "kb_attack",
    "retreat": "kb_retreat",
    "decline": "kb_decline",
    "use nano_patch": "kb_use_nano",
    "factions": "kb_factions",
}


def vk_keyboard(rows: list[list[Any]], lang: str = "ru") -> dict[str, Any]:
    buttons = []
    for row in rows:
        brow = []
        for entry in row:
            if isinstance(entry, tuple):
                cmd, label = entry
            else:
                cmd, label = entry, t(LABEL_KEY.get(entry, entry), lang)
            brow.append(
                {
                    "action": {
                        "type": "text",
                        "label": label[:40],
                        "payload": json.dumps({"cmd": cmd}, ensure_ascii=False),
                    },
                    "color": COLOR_BY_COMMAND.get(cmd.split()[0], "secondary"),
                }
            )
        buttons.append(brow)
    return {"one_time": False, "inline": False, "buttons": buttons}


def empty_keyboard() -> dict[str, Any]:
    return {"one_time": False, "buttons": []}


def _event_choice_rows(snapshot: dict[str, Any], lang: str) -> list[list[Any]]:
    """Кнопки вариантов события: label — текст выбора; отказ — Пропустить/Проскочить."""
    choices = snapshot.get("script", {}).get("choices", [])
    is_enemy = any("battle" in (c.get("outcome") or {}) for c in choices)
    rows: list[list[Any]] = [
        [(f"choice {c.get('key')}", str(c.get("text", c.get("key")))) for c in choices[i : i + 2]]
        for i in range(0, len(choices), 2)
    ]
    if snapshot.get("can_refuse"):
        rows.append([("decline", t("kb_dodge" if is_enemy else "kb_skip", lang))])
    return rows


async def keyboard_for_character(
    session: AsyncSession, character_id: Any
) -> dict[str, Any] | None:
    """Меню по состоянию персонажа и его языку. None — персонажа нет."""
    from app.battles import get_active_battle
    from app.expeditions import _current_offered_event, get_ongoing_expedition
    from app.models_game import Character
    from app.modules import installed_abilities

    character = await session.get(Character, character_id)
    if character is None:
        return None
    lang = character.lang

    battle = await get_active_battle(session, character_id)
    if battle is not None:
        abilities = await installed_abilities(session, character)
        rows: list[list[Any]] = [
            ["attack"] + [str(a["combat_cmd"]) for a in abilities],
            ["use nano_patch", "retreat"],
        ]
        return vk_keyboard(rows, lang)

    expedition = await get_ongoing_expedition(session, character_id)
    if expedition is not None:
        if expedition.status == "returning":
            return vk_keyboard([["exp", "sync"]], lang)
        offered = await _current_offered_event(session, expedition)
        if offered is not None and offered.snapshot.get("kind") == "interactive":
            return vk_keyboard(_event_choice_rows(offered.snapshot, lang), lang)
        # свободное плавание: исследовать / инфо / залатать / вернуться
        return vk_keyboard([["go", "exp", "patch"], ["return"]], lang)

    return vk_keyboard(
        [
            ["me", "inv", "ship"],
            ["quests", "mods", "wallet"],
            ["launch", "shop", "recipes"],
        ],
        lang,
    )


async def keyboard_for_new_user(lang: str = "ru") -> dict[str, Any]:
    return vk_keyboard([["factions"]], lang)
