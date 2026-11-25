"""VK-клавиатуры: кнопки, отправляющие боту текст команды (ТЗ: удобство ввода).

После каждого ответа бот присылает меню, зависящее от состояния игрока:
бой → боевые кнопки; событие с выбором → варианты; экспедиция → go/exp/return;
порт → основное меню. Кнопки text-типа: нажатие шлёт боту label как сообщение
(команды парсятся как обычный текст — весь конвейер идемпотентности работает).
"""
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

# цвета VK: primary (синий), secondary (белый), negative (красный), positive (зелёный)
COLOR_BY_COMMAND = {
    "attack": "positive",
    "go": "positive",
    "launch": "positive",
    "retreat": "negative",
    "return": "secondary",
    "decline": "negative",
    "confirm": "positive",
}


def vk_keyboard(rows: list[list[str]]) -> dict[str, Any]:
    """rows — список рядов, ряд — список команд (label = текст команды)."""
    return {
        "one_time": False,
        "inline": False,
        "buttons": [
            [
                {
                    "action": {"type": "text", "label": cmd, "payload": "{}"},
                    "color": COLOR_BY_COMMAND.get(cmd.split()[0], "secondary"),
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
    """Меню по состоянию персонажа. None — персонажа нет (покажем стартовую подсказку)."""
    from app.battles import get_active_battle
    from app.expeditions import _current_offered_event, get_ongoing_expedition
    from app.models_game import Character
    from app.modules import installed_abilities

    battle = await get_active_battle(session, character_id)
    if battle is not None:
        abilities = await installed_abilities(session, cast(Character, _Char(character_id)))
        rows: list[list[str]] = [
            ["attack"] + [str(a["combat_cmd"]) for a in abilities],
            ["use nano_patch", "retreat"],
        ]
        return vk_keyboard(rows)

    expedition = await get_ongoing_expedition(session, character_id)
    if expedition is not None:
        if expedition.status == "returning":
            return vk_keyboard([["exp", "sync"]])
        offered = await _current_offered_event(session, expedition)
        if offered is not None and offered.snapshot.get("kind") == "interactive":
            keys = [str(c.get("key")) for c in offered.snapshot.get("script", {}).get("choices", [])]
            rows = [keys[i : i + 3] for i in range(0, len(keys), 3)]
            if offered.snapshot.get("can_refuse"):
                rows.append(["decline"])
            return vk_keyboard(rows)
        return vk_keyboard([["go", "exp"], ["return"]])

    # космопорт: основное меню
    return vk_keyboard(
        [
            ["me", "inv", "ship"],
            ["quests", "mods", "wallet"],
            ["launch", "shop", "recipes"],
        ]
    )


class _Char:
    """Минимальная обёртка для installed_abilities (нужен только id)."""

    def __init__(self, character_id: Any) -> None:
        self.id = character_id


async def keyboard_for_new_user() -> dict[str, Any]:
    return vk_keyboard([["factions"]])
