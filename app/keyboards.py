"""VK-клавиатуры: локализованные кнопки; payload несёт команду, label — текст на языке игрока.

VK text-кнопка шлёт боту label, НО при наличии payload бот читает его как команду
(см. adapters/vk/bot.py) — поэтому label можно переводить, не ломая протокол команд.
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.locale import t

# цвета VK
COLOR_BY_COMMAND = {
    "attack": "positive",
    "go": "positive",
    "launch": "positive",
    "retreat": "negative",
    "decline": "negative",
}

# команда → ключ локали для label
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
    "attack": "kb_attack",
    "retreat": "kb_retreat",
    "decline": "kb_decline",
    "use nano_patch": "kb_use_nano",
    "factions": "kb_factions",
}


def vk_keyboard(rows: list[list[str]], lang: str = "ru") -> dict[str, Any]:
    """rows — список рядов, ряд — список команд. label локализован, payload = команда."""
    return {
        "one_time": False,
        "inline": False,
        "buttons": [
            [
                {
                    "action": {
                        "type": "text",
                        "label": t(LABEL_KEY.get(cmd, cmd), lang),
                        "payload": f'{{"cmd": "{cmd}"}}',
                    },
                    "color": COLOR_BY_COMMAND.get(cmd, "secondary"),
                }
                for cmd in row
            ]
            for row in rows
        ],
    }


def empty_keyboard() -> dict[str, Any]:
    return {"one_time": False, "buttons": []}


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
        rows: list[list[str]] = [
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
            keys = [str(c.get("key")) for c in offered.snapshot.get("script", {}).get("choices", [])]
            rows = [keys[i : i + 3] for i in range(0, len(keys), 3)]
            if offered.snapshot.get("can_refuse"):
                rows.append(["decline"])
            return vk_keyboard(rows, lang)
        return vk_keyboard([["go", "exp"], ["return"]], lang)

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
