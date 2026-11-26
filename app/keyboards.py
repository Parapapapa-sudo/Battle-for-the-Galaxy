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
    "intro": "kb_start",
    "intro2": "kb_continue",
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
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        # идёт ремонт («Залатать») — только «Инфо»
        if expedition.patch_eta is not None:
            return vk_keyboard([["exp"]], lang)
        # в пути между событиями — только «Инфо»
        if expedition.next_event_at is not None and expedition.next_event_at > now:
            return vk_keyboard([["exp"]], lang)
        # свободное плавание: [Исследовать][Залатать корабль] / [Инфо][В космопорт]
        return vk_keyboard([["go", "patch"], ["exp", "return"]], lang)

    # космопорт: залы (ТЗ заказчика). Основной — точка входа после экспедиции.
    hall = character.hall or "main"
    if hall == "workshop":
        return vk_keyboard(
            [
                ["mods", "recipes"],
                ["inv", "ship"],
                [("hall main", t("kb_hall_main", lang))],
            ],
            lang,
        )
    if hall == "hq":
        return vk_keyboard(
            [
                ["quests", "shop"],
                [("hall main", t("kb_hall_main", lang))],
            ],
            lang,
        )
    return vk_keyboard(
        [
            ["launch", "me"],
            [("hall workshop", t("kb_workshop", lang)), ("hall hq", t("kb_hq", lang))],
        ],
        lang,
    )


async def keyboard_for_account(session: AsyncSession, account: Any) -> dict[str, Any]:
    """Клавиатура онбординга (персонажа ещё нет): шаги из account.onboarding."""
    from app import characters

    onboarding = account.onboarding or {}
    step = onboarding.get("step")
    if step == "preview":
        # просмотр фракции: подтверждение выбора или возврат к списку
        faction = await characters.get_faction_by_code(session, str(onboarding.get("preview", "")))
        if faction is not None:
            return vk_keyboard(
                [
                    [(f"pick {faction.code}", t("kb_choose_faction"))],
                    [("intro2", t("kb_back_factions"))],
                ]
            )
        return vk_keyboard([["intro2"]])
    if step in ("intro2", "join"):
        factions = await characters.list_factions(session)
        rows = [
            [(f"join {f.code}", f.name) for f in factions[i : i + 2]]
            for i in range(0, len(factions), 2)
        ]
        return vk_keyboard(rows)
    if step == "intro":
        return vk_keyboard([["intro2"]])
    return vk_keyboard([["intro"]])


async def keyboard_for_new_user(lang: str = "ru") -> dict[str, Any]:
    return vk_keyboard([["intro"]], lang)
