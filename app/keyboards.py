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
    "accept": "positive",
    "craft": "positive",
    "track": "primary",
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
    # входящие переводы ждут подтверждения — кнопки в любом зале (редко, ≤2)
    transfer_rows = await _pending_transfer_rows(session, character, lang)

    if hall == "workshop":
        ws_rows: list[list[Any]] = [
            ["mods", "recipes"],
            ["inv", "ship"],
        ]
        pool: list[list[Any]] = []
        # крафт — только то, на что хватает материалов
        from app import crafting as crafting_app

        for recipe in await crafting_app.list_recipes(session, character):
            if recipe["can_craft"]:
                pool.append(
                    [(f"craft {recipe['code']}", t("kb_craft_recipe", lang, name=recipe["name"]))]
                )
        # отмена активных заказов
        from app.models_game import RecipeTemplate

        for idx, job in enumerate(await crafting_app.running_jobs(session, character), start=1):
            job_recipe = await session.get(RecipeTemplate, job.recipe_id)
            pool.append(
                [
                    (
                        f"craftcancel {idx}",
                        t("kb_cancel_job", lang, name=job_recipe.name if job_recipe else "?"),
                    )
                ]
            )
        # модули: установка/снятие и тренировка — кнопками, коды скрыты
        from app import modules as modules_app

        for mod in await modules_app.list_modules(session, character):
            if mod["installed"]:
                pool.append(
                    [(f"uninstall {mod['code']}", t("kb_uninstall", lang, name=mod["name"]))]
                )
            else:
                pool.append(
                    [(f"install {mod['code']}", t("kb_install", lang, name=mod["name"]))]
                )
            pool.append(
                [(f"train {mod['code']} 10", t("kb_train10", lang, name=mod["name"]))]
            )
        page_rows, page, total = _paginate(pool, character.hall_page)
        ws_rows += transfer_rows + page_rows
        if total > 1:
            ws_rows.append([("page next", t("kb_page_next", lang, n=page + 1, total=total))])
        ws_rows.append([("hall main", t("kb_hall_main", lang))])
        return vk_keyboard(ws_rows, lang)
    if hall == "hq":
        hq_rows: list[list[Any]] = [["quests", "shop"]]
        pool = []
        # принятие доступных заданий и переключение отслеживания — прямо кнопками
        from app import market as market_app
        from app import quests as quests_mod

        for _state, quest in await quests_mod.available_quests(session, character):
            pool.append([(f"accept {quest.code}", t("kb_accept_quest", lang, name=quest.name))])
        for _state, quest in await quests_mod.active_quests(session, character):
            if quest.code != character.tracked_quest:
                pool.append([(f"track {quest.code}", t("kb_track_quest", lang, name=quest.name))])
        # покупки — кнопками вместо кодов предложений
        for offer in await market_app.list_offers(session):
            pool.append(
                [(f"buy {offer.code}", t("kb_buy", lang, title=market_app.offer_title(offer, lang)))]
            )
        page_rows, page, total = _paginate(pool, character.hall_page)
        hq_rows += transfer_rows + page_rows
        if total > 1:
            hq_rows.append([("page next", t("kb_page_next", lang, n=page + 1, total=total))])
        hq_rows.append([("hall main", t("kb_hall_main", lang))])
        return vk_keyboard(hq_rows, lang)
    main_rows: list[list[Any]] = [
        ["launch", "me"],
        [("hall workshop", t("kb_workshop", lang)), ("hall hq", t("kb_hq", lang))],
    ]
    main_rows += transfer_rows
    return vk_keyboard(main_rows, lang)


# Сколько «действующих» кнопок показываем на одной странице зала
# (с навигацией, пейджером и «В основной зал» укладываемся в лимит VK — 10 рядов)
PAGE_SIZE = 4


def _paginate(
    pool: list[list[Any]], page: int
) -> tuple[list[list[Any]], int, int]:
    """Страницы кнопок: страница зациклена (кнопка «Далее ▶» листает по кругу)."""
    if not pool:
        return [], 0, 0
    total = (len(pool) + PAGE_SIZE - 1) // PAGE_SIZE
    page = page % total
    return pool[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, total


async def _pending_transfer_rows(
    session: AsyncSession, character: Any, lang: str
) -> list[list[Any]]:
    """Кнопки подтверждения входящих переводов (вместо confirm/decline <id>)."""
    from app import economy

    rows: list[list[Any]] = []
    for transfer in (await economy.pending_transfers(session, character))[:2]:
        short_id = transfer.id.hex[:8]
        rows.append(
            [
                (f"confirm {short_id}", t("kb_accept_transfer", lang)),
                (f"decline {short_id}", t("kb_decline_transfer", lang)),
            ]
        )
    return rows


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
