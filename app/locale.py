"""i18n: все пользовательские тексты и кнопки на двух языках.

Команды НЕ переводятся (protocol stable). Активен русский (`characters.lang`,
по умолчанию "ru"); английский — для будущего переключателя, таблица EN
заполнена и используется через t(key, lang="en").
"""
from typing import Any

RU: dict[str, str] = {
    # account scope
    "usage_no_character": "У вас ещё нет персонажа. Создайте: start <имя> <код_фракции>. Список фракций: factions",
    "factions_empty": "Фракции пока не созданы (контент не засеян).",
    "factions_title": "Фракции:",
    "ui_reloaded": "Меню обновлено.",
    # онбординг (первый вход игрока)
    "kb_start": "Начать", "kb_continue": "Продолжить",
    "welcome_new": (
        "Вступайте в битву за галактику!\n\n"
        "Нажмите (или напишите) «Начать», чтобы зайти в игру."
    ),
    "intro_overview": (
        "Это текстовая онлайн-ММО-игра «Битва за галактику»: все действия выполняются "
        "командами под окном чата, а результаты приходят ответом от сообщества.\n\n"
        "🚀 Тебя ждут:\n\n"
        "Выбор одной из четырёх фракций.\n"
        "Бесконечные космические экспедиции и случайные события.\n"
        "Пошаговые сражения с различными противниками.\n"
        "Развитие персонажа, корабля, способностей и навыков.\n"
        "Поиск редких предметов, создание оборудования и улучшение модулей.\n"
        "Торговля с другими игроками и аукцион.\n"
        "Сюжетные задания, открытия и новые возможности.\n\n"
        "⚔️ Исследуй. Сражайся. Развивайся.\n\n"
        "Собирай свой корабль, находи собственный стиль игры, добывай редкие ресурсы "
        "и стань частью истории галактики.\n\n"
        "Твоя история начинается здесь."
    ),
    "intro_lore": (
        "Долгие века галактикой правили четыре силы. Астрийский Конкордат наводил "
        "порядок силой, Вольная Лига защищала свободу независимых миров, Пепельный "
        "Предел выживал на забытых окраинах, а древний Синод Эонов хранил знания, "
        "способные спасти галактику — или погубить её.\n\n"
        "Теперь старые договоры истлевают, границы горят, и каждый капитан сам "
        "решает, под чьим знаменем лететь.\n\n"
        "Выбери фракцию — она определит твой стартовый корабль и бонус. "
        "Нажми на название, чтобы узнать её историю:"
    ),
    "join_prompt_name": (
        "Напиши имя капитана одним сообщением (от {low} до {high} символов) — и в бой!"
    ),
    "join_preview_hint": (
        "Если решишь лететь под этим знаменем — нажми «Выбрать эту фракцию». "
        "Хочешь сравнить — вернись к списку."
    ),
    "pick_confirmed": "✅ Фракция выбрана: {faction}.",
    "kb_choose_faction": "Выбрать эту фракцию",
    "kb_back_factions": "◀ К списку фракций",
    "start_usage": "Создание персонажа: start <имя> <код_фракции>.\nДоступные фракции: {codes}",
    "start_no_factions": "нет (контент не засеян)",
    "start_choose": "Выберите фракцию для «{name}»:\n{factions}",
    "start_created": (
        "Персонаж «{name}» создан. Фракция: {faction}.\n\n"
        "Ты в космопорту. Первый шаг — нажми «Экспедиция», чтобы отправиться "
        "в первый вылет: задания (зал «Штаб» → «Задания») будут подсказывать, "
        "что делать дальше.\n"
        "Если пропали кнопки — напиши ui."
    ),
    # profile / inventory / ship
    "profile": "👤 {name} — {faction}\n\n⭐ Репутация: {rep}\n\n✨ Опыт (пул): {xp}",
    "inv_bad_kind": "Раздел: ship | warehouse | waiting",
    "inv_empty": "📦 Инвентарь пуст.",
    "inv_header": "📦 Инвентарь ({kind}, {used}/{limit}):",
    "inv_item": "[{qty}×] {name} ({code})",
    "ship_card": "🚀 {name}\n\n🛡 Прочность: {hull}/{max} (поломки: {mal})\n\n⛽ Топливо: {fuel}\n\n📍 Статус: {status}",
    # expeditions
    "exp_not_started": "🛰 Экспедиция не начата. Нажмите «Экспедиция».",
    "exp_returning": "🛬 Возвращение в космопорт ({reason}).\n\nГлубина: {depth}. Осталось топлива: {fuel}.",
    "exp_active": "🛰 Экспедиция активна.\n\nГлубина: {depth}. Топливо: {fuel}/{start}.",
    "exp_hint": "Кнопки: «Исследовать» — дальше, «В космопорт» — вернуться.",
    "launch_started": "🚀 Экспедиция началась! Топливо: {fuel}.\n\nНажимайте «Исследовать», чтобы двигаться дальше.",
    "choice_missing_arg": "Укажите вариант: choice <n>",
    "decline_transfer": "Передача отклонена. Активы отправителю возвращены.",
    "decline_event": "Отказ от события «{name}». Нажмите «Исследовать», чтобы идти дальше.",
    "return_started": "🛬 Возвращение началось. Сообщение о прибытии придёт автоматически.",
    "cooldown": "Сканирование сектора… следующее событие через {wait}с.",
    # battle
    "battle_none": "Нет активного боя.",
    "blog_header": "📓 Боевой журнал:",
    "blog_turn": "— ход {no} ({actor}): {summary}",
    "blog_detail": "    · {entry}",
    # modules
    "mods_empty": "🔩 У вас нет модулей.",
    "mods_header": "🔩 Опыт (пул): {xp}. Сбросов сборки: {resets}.",
    "mods_item": "[{kind}] {name} ({code}) — ур. {level} ({xp} опыта), {status}{extra}",
    "mods_status_slot": "слот {slot}",
    "mods_status_inv": "в инвентаре",
    "mods_extra_attack": ", атака +{bonus}",
    "mods_extra_ability": ", команда боя: {cmd}",
    "mods_hint": "Команды: install <код> | uninstall <код> | train <код> <опыт> | resetbuild",
    "module_missing": "Укажите модуль: {cmd} <код>",
    "install_ok": "Модуль «{code}» установлен.",
    "uninstall_ok": "Модуль «{code}» снят (прогресс сохранён).",
    "train_bad": "Формат: train <код> <опыт>",
    "train_ok": "Вложено {xp} опыта в «{code}». Остаток пула: {pool}.",
    "reset_ok": "Сборка сброшена: {returned} опыта возвращено в пул (сейчас {pool}). Модули остались установленными.",
    # crafting
    "recipes_empty": "🧪 Нет доступных рецептов.",
    "recipes_title": "🧪 Рецепты",
    "recipe_line": "«{name}» → {qty}× {result} ({duration} с)",
    "recipe_materials": "Материалы: {list}",
    "recipe_mat_item": "{name} {have}/{need}",
    "recipe_ready": "✅ Готов",
    "recipe_no_materials": "❌ Не хватает материалов",
    "recipe_missing": "   не хватает: {list}",
    "recipes_slots": "🛠 Занято слотов: {used}/{total}.",
    "craft_bad": "Формат: craft <код_рецепта>",
    "craft_started": "🛠 Производство «{code}» запущено (готово через {seconds}с).",
    "crafting_empty": "Нет активных заказов.",
    "crafting_slots": "Слоты: {used}/{total}",
    "crafting_item": "{idx}. {name} — до {time}",
    "crafting_hint": "Отмена: craftcancel <номер>",
    "store_bad": "Формат: store <код> <количество>",
    "store_ok": "Перемещено на склад: {qty}× {code}.",
    "take_bad": "Формат: take <код> <количество>",
    "take_ok": "Взято со склада: {qty}× {code}.",
    # economy
    "wallet": "💰 Эоны: {eon} (зарезервировано {eon_r})\n\n🪙 Кванты: {quant} (зарезервировано {quant_r})",
    "send_bad": "Формат: send <имя> <кванты>",
    "send_pending": "Предложение отправлено «{name}» — ждёт подтверждения.",
    "send_ok": "Отправлено {amount} Квантов игроку «{name}».",
    "senditem_bad": "Формат: senditem <имя> <код> <количество>",
    "senditem_pending": "Предложение отправлено «{name}» — ждёт подтверждения.",
    "senditem_ok": "Отправлено {qty}× {code} игроку «{name}».",
    "inbox_empty": "📨 Входящих предложений нет.",
    "inbox_title": "📨 Входящие предложения:",
    "inbox_item": "{id8}: от «{sender}» — {quant} Квантов, {items} предметов. confirm {id8} | decline {id8}",
    "confirm_ok": "Передача подтверждена и исполнена.",
    "sell_ok": "Лот создан: {qty}× {code}, мин. {price} Квантов. ID: {id8}",
    "lots_empty": "Активных лотов нет.",
    "lots_title": "⚖️ Аукцион:",
    "lots_item": "{id8}: {qty}× {code}, мин {min}{top}{mine}. bid {id8} <цена>",
    "lots_mine": " (ваш)",
    "lots_top": ", ставка {top}",
    "bid_ok": "Ставка принята: {code}, ваша цена {amount}.",
    "bid_buyout": "Продано с выкупом: {code}, ваша цена {amount}.",
    "market_order": "Ордер {side} размещён: {amount} Эонов по {price} Квантов. ID: {id8}",
    "market_help": "Внутренний рынок Эонов. Ориентир: 1 Эон ≈ {reference} Квантов.\nmarket buy <эоны> <цена> — купить Эоны за Кванты\nmarket sell <эоны> <цена> — продать Эоны за Кванты\nСведение ордеров — автоматически (worker).",
    "shop_empty": "🛒 Магазин пуст.",
    "shop_title": "🛒 Магазин",
    "shop_section_rub": "💎 Эоны (оплата в рублях):",
    "shop_pack_item": "— {eons} Эонов за {rub} ₽ · buy {code}",
    "shop_section_eon": "⭐ Подписки (за Эоны):",
    "shop_sub_item": "— Подписка на {days} дн. — {price} Эонов · buy {code}",
    "shop_hint": "Покупка: buy <код> (например, buy eon_pack_100).",
    "shop_unknown": "Предложение «{code}» не найдено — смотрите «Магазин».",
    "buy_pack_order": (
        "💳 Счёт создан: {eons} Эонов за {rub} ₽.\n\n"
        "Оплатить: {url}\n\n"
        "Эоны начислятся автоматически сразу после оплаты."
    ),
    "buy_sub_ok": (
        "✅ Подписка на {days} дн. активирована (−{price} Эонов).\n\n"
        "Бонусы: расширенный инвентарь и дополнительный слот крафта."
    ),
    "buy_ok": "Покупка «{code}» оплачена ({status}).",
    # sync / quests
    "sync_empty": "✅ Все таймеры в норме, застреваний нет.",
    "sync_fired": "Обработано просроченных событий: {names}.",
    "quests_title": "📜 Задания:",
    "quest_item": "«{name}»\n{description}\nПрогресс: {progress}",
    "quests_none": "📜 Активных заданий нет.",
    "quest_progress_line": "Прогресс: {progress}",
    "quest_tracked_mark": " 👁 (отслеживается)",
    "quests_available_title": "Доступные задания:",
    "quest_available_item": "«{name}»\n{description}",
    "accept_ok": "✅ Задание принято: «{name}». Оно отслеживается — прогресс будет приходить в чат.",
    "track_ok": "👁 Отслеживается задание: «{name}».",
    "quest_progress": "📜 «{name}»: {progress}",
    "quest_completed": "🏆 Задание «{name}» выполнено! Награды начислены.",
    "quest_new_available": (
        "📜 Доступно новое задание: «{name}».\n{description}\n"
        "Примите его: Штаб → «Задания» → кнопка «Принять»."
    ),
    "obj_expedition_start": "вылетов в экспедицию",
    "obj_event_resolved": "событий разрешено",
    "obj_battle_won": "побед в бою",
    "obj_crafted": "предметов создано",
    "fevents_title": "🌐 Фракционные события:",
    "fevent_item": "— {name}: общий прогресс {total}/{goal} (до {until})",
    "unknown_command": "Неизвестная команда: {cmd}",
    # клавиатуры (label; payload — всегда команда)
    "kb_me": "О игроке", "kb_inv": "Инвентарь", "kb_ship": "О корабле",
    "kb_quests": "Задания", "kb_mods": "Модули", "kb_wallet": "wallet",
    "kb_launch": "Экспедиция", "kb_shop": "Магазин", "kb_recipes": "Крафт",
    "kb_workshop": "Мастерская", "kb_hq": "Штаб", "kb_hall_main": "В основной зал",
    "kb_accept_quest": "Принять «{name}»",
    "kb_craft_recipe": "Создать «{name}»",
    "kb_track_quest": "Отслеживать «{name}»",
    "hall_main": "🏢 Основной зал космопорта.",
    "hall_workshop": "🔧 Мастерская: модули, крафт, инвентарь и корабль.",
    "hall_hq": "🎖 Штаб: задания и магазин.",
    "bad_hall": "Неизвестный зал.",
    "hall_in_expedition": "Залы космопорта доступны только в порту.",
    "kb_go": "Исследовать", "kb_exp": "Инфо", "kb_return": "В космопорт", "kb_sync": "sync",
    "kb_patch": "Залатать", "kb_skip": "Пропустить", "kb_dodge": "Проскочить",
    "patch_started": "🔧 Ремонт начался: прочность будет восстановлена через {delay}с.",
    "patch_done": "🔧 Корабль залатан: прочность {hull}/{cap}.",
    "patch_eta_line": "🔧 Идёт ремонт корабля — осталось {wait}с.",
    # сводка состояния после события
    "status_title": "📊 Состояние корабля",
    "status_hull": "🛡 Прочность: {cur}/{max}",
    "status_malfunctions": "⚙️ Поломки: {n}",
    "status_inventory": "📦 Инвентарь: свободно {free}/{total} слотов",
    "status_inventory_unlimited": "📦 Инвентарь: без лимита",
    "hull_too_low": "🛡 Прочность вашего корабля не позволяет продолжить исследование космоса.",
    "travel_started": "🚀 Вы отправились в путешествие по просторам космоса. Впереди ждут приключения!",
    "fuel_line": "⛽ Топливо: {cur}/{max}",
    "travel_eta": "⏳ Следующее событие через {wait}с.",
    "kb_attack": "Атака", "kb_retreat": "Отступить", "kb_decline": "decline",
    "kb_use_nano": "Ремонт", "kb_factions": "factions",
    # события (describe_event)
    "event_title": "🌌 Событие «{name}» (опасность: {danger})",
    "event_refuse": "Отказ: decline",
    "event_outcome": "📖 Исход: {message}",
    "effect_got": "получено {qty}× {code}",
    "effect_lost_full": "инвентарь полон: потеряно {qty}× {code}",
    "effect_hull_damage": "урон корпусу: -{damage}",
    "effect_ship_down": "КОРАБЛЬ ОТКАЗАЛ — отступаем в космопорт",
    "danger_low": "низкая", "danger_medium": "средняя", "danger_high": "высокая",
    # возвращение
    "ret_started": "🛬 Возвращение началось. Прибытие через {delay}с.\n\nТопливо сохранено: {fuel}.",
    "ret_fuel_empty": "⛽ Топливо исчерпано — корабль возвращается автоматически.",
    "ret_fuel_empty_note": "⛽ Топливо исчерпано — корабль автоматически возвращается в космопорт (поломок: +{mal}).",
    "ret_defeat": "💥 Поражение. Корабль отступает в космопорт.",
    "arrived": "🛬 Прибыли в космопорт.\n\nЭкспедиция завершена: глубина {depth}, топлива осталось {fuel}.",
    # бой (describe + сводки)
    "battle_card": "⚔️ БОЙ: {name} [{category}]",
    "battle_enemy": "👾 Противник: {cur}/{max}",
    "battle_you": "🚀 Ваш корабль: {hull}/{max} (поломки: {mal})",
    "battle_turn": "🎯 Ход: {n}",
    "battle_passives": "Особенности противника: {list}",
    "battle_weaknesses": "Замечены слабости: {list}",
    "battle_retreat_hint": "Отступление: попытка 1/1, шанс {label}.",
    "chance_low": "низкий", "chance_medium": "средний", "chance_high": "высокий",
    "sum_enemy_attack": "{name} атакует: -{damage} прочности.",
    "sum_enemy_ability": "{name} использует «{ability}»: -{damage} прочности.",
    "sum_player_attack": "Ваш удар: -{damage} прочности противнику.",
    "sum_victory": "Победа! +{xp} опыта.{loot}",
    "sum_victory_loot": " Добыча: {list}.",
    "sum_defeat": "Корабль выведен из строя — отступаем в космопорт.",
    "sum_retreat_fail": "Отступление не удалось — противник перехватывает вас!",
    "sum_retreat_ok": "Вы уходите из боя. Награда за победу не выдаётся.",
    "sum_retreat_damage": "Прощальный залп: -{damage} прочности.",
    "sum_retreat_mal": "Получена поломка.",
    "sum_use_heal": "Использован {name}: +{healed} прочности.",
    "sum_use_plain": "Использован {name}.",
    "intro_fight": "⚔ Бой начался: {name} [{category}]",
    "hp_player": "HP игрока: {cur}/{max}",
    "hp_enemy": "HP противника: {cur}/{max}",
    "victory_title": "🏆 Вы победили!",
    "victory_reward": "Награда: {list}",
    "victory_space": "Осталось места в инвентаре: {free}/{size}",
    "defeat_title": "💥 Корабль выведен из строя. Дождитесь окончания ремонта (~{delay}с), после чего вы автоматически отправитесь в космопорт.",
}

EN: dict[str, str] = {
    "usage_no_character": "You don't have a character yet. Create one: start <name> <faction_code>. Faction list: factions",
    "factions_empty": "No factions yet (content not seeded).",
    "factions_title": "Factions:",
    "ui_reloaded": "Menu reloaded.",
    "kb_start": "Start", "kb_continue": "Continue",
    "welcome_new": (
        "Join the battle for the galaxy!\n\n"
        "Press (or type) «Start» to enter the game."
    ),
    "intro_overview": (
        "This is a text-based online MMO «Battle for the Galaxy»: every action is a "
        "command below the chat window, and results arrive as community replies.\n\n"
        "🚀 Awaiting you:\n\n"
        "A choice of four factions.\n"
        "Endless space expeditions and random events.\n"
        "Turn-based battles with various enemies.\n"
        "Progression of character, ship, abilities and skills.\n"
        "Rare items, crafting equipment and module upgrades.\n"
        "Trading with other players and an auction house.\n"
        "Story quests, discoveries and new possibilities.\n\n"
        "⚔️ Explore. Fight. Evolve.\n\n"
        "Build your ship, find your own playstyle, gather rare resources "
        "and become part of the galaxy's history.\n\n"
        "Your story begins here."
    ),
    "intro_lore": (
        "For centuries the galaxy was shaped by four powers. The Astrian Concord "
        "enforced order through strength, the Free League defended the independence "
        "of its worlds, the Ashen Reach survived on forgotten frontiers, and the "
        "ancient Eon Synod guarded knowledge that could save the galaxy — or doom it.\n\n"
        "Now old treaties are crumbling, borders are burning, and every captain "
        "chooses their own banner.\n\n"
        "Choose a faction — it defines your starting ship and bonus. "
        "Tap a name to learn its story:"
    ),
    "join_prompt_name": (
        "Send your captain's name as a single message ({low}–{high} characters) — and to battle!"
    ),
    "join_preview_hint": (
        "If you decide to fly under this banner — press «Choose this faction». "
        "Want to compare — go back to the list."
    ),
    "pick_confirmed": "✅ Faction chosen: {faction}.",
    "kb_choose_faction": "Choose this faction",
    "kb_back_factions": "◀ Back to factions",
    "start_usage": "Character creation: start <name> <faction_code>.\nAvailable factions: {codes}",
    "start_no_factions": "none (content not seeded)",
    "start_choose": "Choose a faction for «{name}»:\n{factions}",
    "start_created": (
        "Character «{name}» created. Faction: {faction}.\n\n"
        "You are at the spaceport. First step — press «Expedition» to set off "
        "on your first flight: quests (HQ hall → «Quests») will guide you further.\n"
        "If buttons disappear — type ui."
    ),
    "profile": "👤 {name} — {faction}\n\n⭐ Reputation: {rep}\n\n✨ XP pool: {xp}",
    "inv_bad_kind": "Section: ship | warehouse | waiting",
    "inv_empty": "📦 Inventory is empty.",
    "inv_header": "📦 Inventory ({kind}, {used}/{limit}):",
    "inv_item": "[{qty}×] {name} ({code})",
    "ship_card": "🚀 {name}\n\n🛡 Hull: {hull}/{max} (malfunctions: {mal})\n\n⛽ Fuel: {fuel}\n\n📍 Status: {status}",
    "exp_not_started": "🛰 No expedition yet. Press «Expedition».",
    "exp_returning": "🛬 Returning to the spaceport ({reason}). Depth: {depth}. Fuel left: {fuel}.",
    "exp_active": "🛰 Expedition active. Depth: {depth}. Fuel: {fuel}/{start}.",
    "exp_hint": "Buttons: «Explore» — move on, «To spaceport» — return.",
    "launch_started": "🚀 Expedition started! Fuel: {fuel}. Press «Explore» to move on.",
    "choice_missing_arg": "Pick an option: choice <n>",
    "decline_transfer": "Transfer declined. Assets returned to the sender.",
    "decline_event": "Declined event «{name}». Press «Explore» to move on.",
    "return_started": "🛬 Return started. Arrival message will come automatically.",
    "cooldown": "Scanning the sector… next event in {wait}s.",
    "battle_none": "No active battle.",
    "blog_header": "📓 Battle log:",
    "blog_turn": "— turn {no} ({actor}): {summary}",
    "blog_detail": "    · {entry}",
    "mods_empty": "🔩 You have no modules.",
    "mods_header": "🔩 XP pool: {xp}. Build resets: {resets}.",
    "mods_item": "[{kind}] {name} ({code}) — lvl {level} ({xp} xp), {status}{extra}",
    "mods_status_slot": "slot {slot}",
    "mods_status_inv": "in inventory",
    "mods_extra_attack": ", attack +{bonus}",
    "mods_extra_ability": ", battle command: {cmd}",
    "mods_hint": "Commands: install <code> | uninstall <code> | train <code> <xp> | resetbuild",
    "module_missing": "Specify module: {cmd} <code>",
    "install_ok": "Module «{code}» installed.",
    "uninstall_ok": "Module «{code}» removed (progress kept).",
    "train_bad": "Format: train <code> <xp>",
    "train_ok": "Invested {xp} XP into «{code}». Pool left: {pool}.",
    "reset_ok": "Build reset: {returned} XP returned to the pool (now {pool}). Modules stay installed.",
    "recipes_empty": "🧪 No recipes available.",
    "recipes_title": "🧪 Recipes",
    "recipe_line": "«{name}» → {qty}× {result} ({duration} s)",
    "recipe_materials": "Materials: {list}",
    "recipe_mat_item": "{name} {have}/{need}",
    "recipe_ready": "✅ Ready",
    "recipe_no_materials": "❌ Missing materials",
    "recipe_missing": "   missing: {list}",
    "recipes_slots": "🛠 Slots used: {used}/{total}.",
    "craft_bad": "Format: craft <recipe_code>",
    "craft_started": "🛠 Production «{code}» started (ready in {seconds}s).",
    "crafting_empty": "No active jobs.",
    "crafting_slots": "Slots: {used}/{total}",
    "crafting_item": "{idx}. {name} — until {time}",
    "crafting_hint": "Cancel: craftcancel <number>",
    "store_bad": "Format: store <code> <qty>",
    "store_ok": "Moved to warehouse: {qty}× {code}.",
    "take_bad": "Format: take <code> <qty>",
    "take_ok": "Taken from warehouse: {qty}× {code}.",
    "wallet": "💰 Eons: {eon} (reserved {eon_r})\n\n🪙 Quants: {quant} (reserved {quant_r})",
    "send_bad": "Format: send <name> <quants>",
    "send_pending": "Offer sent to «{name}» — awaiting confirmation.",
    "send_ok": "Sent {amount} Quants to «{name}».",
    "senditem_bad": "Format: senditem <name> <code> <qty>",
    "senditem_pending": "Offer sent to «{name}» — awaiting confirmation.",
    "senditem_ok": "Sent {qty}× {code} to «{name}».",
    "inbox_empty": "📨 No incoming offers.",
    "inbox_title": "📨 Incoming offers:",
    "inbox_item": "{id8}: from «{sender}» — {quant} Quants, {items} items. confirm {id8} | decline {id8}",
    "confirm_ok": "Transfer confirmed and executed.",
    "sell_ok": "Lot created: {qty}× {code}, min {price} Quants. ID: {id8}",
    "lots_empty": "No active lots.",
    "lots_title": "⚖️ Auction:",
    "lots_item": "{id8}: {qty}× {code}, min {min}{top}{mine}. bid {id8} <price>",
    "lots_mine": " (yours)",
    "lots_top": ", bid {top}",
    "bid_ok": "Bid placed: {code}, your price {amount}.",
    "bid_buyout": "Buyout: {code}, your price {amount}.",
    "market_order": "{side} order placed: {amount} Eons at {price} Quants. ID: {id8}",
    "market_help": "Internal Eon market. Reference: 1 Eon ≈ {reference} Quants.\nmarket buy <eons> <price> — buy Eons with Quants\nmarket sell <eons> <price> — sell Eons for Quants\nOrders are matched automatically (worker).",
    "shop_empty": "🛒 Shop is empty.",
    "shop_title": "🛒 Shop",
    "shop_section_rub": "💎 Eons (pay in rubles):",
    "shop_pack_item": "— {eons} Eons for {rub} ₽ · buy {code}",
    "shop_section_eon": "⭐ Subscriptions (for Eons):",
    "shop_sub_item": "— {days}-day subscription — {price} Eons · buy {code}",
    "shop_hint": "To buy: buy <code> (e.g. buy eon_pack_100).",
    "shop_unknown": "Offer «{code}» not found — see the Shop.",
    "buy_pack_order": (
        "💳 Invoice created: {eons} Eons for {rub} ₽.\n\n"
        "Pay: {url}\n\n"
        "Eons are credited automatically right after payment."
    ),
    "buy_sub_ok": (
        "✅ {days}-day subscription activated (−{price} Eons).\n\n"
        "Bonuses: bigger inventory and an extra crafting slot."
    ),
    "buy_ok": "Purchase «{code}» paid ({status}).",
    "sync_empty": "✅ All timers are fine, nothing is stuck.",
    "sync_fired": "Processed overdue events: {names}.",
    "quests_title": "📜 Quests:",
    "quest_item": "«{name}»\n{description}\nProgress: {progress}",
    "quests_none": "📜 No active quests.",
    "quest_progress_line": "Progress: {progress}",
    "quest_tracked_mark": " 👁 (tracked)",
    "quests_available_title": "Available quests:",
    "quest_available_item": "«{name}»\n{description}",
    "accept_ok": "✅ Quest accepted: «{name}». It is now tracked — progress will come to the chat.",
    "track_ok": "👁 Tracking quest: «{name}».",
    "quest_progress": "📜 «{name}»: {progress}",
    "quest_completed": "🏆 Quest «{name}» completed! Rewards granted.",
    "quest_new_available": (
        "📜 New quest available: «{name}».\n{description}\n"
        "Accept it: HQ → «Quests» → «Accept» button."
    ),
    "obj_expedition_start": "expeditions launched",
    "obj_event_resolved": "events resolved",
    "obj_battle_won": "battles won",
    "obj_crafted": "items crafted",
    "fevents_title": "🌐 Faction events:",
    "fevent_item": "— {name}: common progress {total}/{goal} (until {until})",
    "unknown_command": "Unknown command: {cmd}",
    "kb_me": "About player", "kb_inv": "Inventory", "kb_ship": "About ship",
    "kb_quests": "Quests", "kb_mods": "Modules", "kb_wallet": "wallet",
    "kb_launch": "Expedition", "kb_shop": "Shop", "kb_recipes": "Craft",
    "kb_workshop": "Workshop", "kb_hq": "HQ", "kb_hall_main": "To main hall",
    "kb_accept_quest": "Accept «{name}»",
    "kb_craft_recipe": "Craft «{name}»",
    "kb_track_quest": "Track «{name}»",
    "hall_main": "🏢 Spaceport main hall.",
    "hall_workshop": "🔧 Workshop: modules, crafting, inventory and ship.",
    "hall_hq": "🎖 HQ: quests and shop.",
    "bad_hall": "Unknown hall.",
    "hall_in_expedition": "Spaceport halls are only available in port.",
    "kb_go": "Explore", "kb_exp": "Info", "kb_return": "To spaceport", "kb_sync": "sync",
    "kb_patch": "Patch", "kb_skip": "Skip", "kb_dodge": "Slip past",
    "patch_started": "🔧 Repairs started: hull will be restored in {delay}s.",
    "patch_done": "🔧 Ship patched: hull {hull}/{cap}.",
    "patch_eta_line": "🔧 Repairing the ship — {wait}s left.",
    "status_title": "📊 Ship status",
    "status_hull": "🛡 Hull: {cur}/{max}",
    "status_malfunctions": "⚙️ Malfunctions: {n}",
    "status_inventory": "📦 Inventory: {free}/{total} slots free",
    "status_inventory_unlimited": "📦 Inventory: unlimited",
    "hull_too_low": "🛡 Your ship's hull integrity is too low to continue exploring space.",
    "travel_started": "🚀 You set off on a journey through the vastness of space. Adventures await!",
    "fuel_line": "⛽ Fuel: {cur}/{max}",
    "travel_eta": "⏳ Next event in {wait}s.",
    "kb_attack": "Attack", "kb_retreat": "Retreat", "kb_decline": "decline",
    "kb_use_nano": "Repair", "kb_factions": "factions",
    "event_title": "🌌 Event «{name}» (danger: {danger})",
    "event_refuse": "Decline: decline",
    "event_outcome": "📖 Outcome: {message}",
    "effect_got": "received {qty}× {code}",
    "effect_lost_full": "inventory full: lost {qty}× {code}",
    "effect_hull_damage": "hull damage: -{damage}",
    "effect_ship_down": "SHIP DOWN — retreating to the spaceport",
    "danger_low": "low", "danger_medium": "medium", "danger_high": "high",
    "ret_started": "🛬 Return started. Arrival in {delay}s. Fuel kept: {fuel}.",
    "ret_fuel_empty": "⛽ Fuel exhausted — the ship returns automatically.",
    "ret_fuel_empty_note": "⛽ Fuel exhausted — the ship is returning to the spaceport automatically (+{mal} malfunctions).",
    "ret_defeat": "💥 Defeat. The ship retreats to the spaceport.",
    "arrived": "🛬 Arrived at the spaceport. Expedition finished (depth {depth}, fuel left {fuel}).",
    "battle_card": "⚔️ BATTLE: {name} [{category}]",
    "battle_enemy": "👾 Enemy: {cur}/{max}",
    "battle_you": "🚀 Your ship: {hull}/{max} (malfunctions: {mal})",
    "battle_turn": "🎯 Turn: {n}",
    "battle_passives": "Enemy traits: {list}",
    "battle_weaknesses": "Weaknesses spotted: {list}",
    "battle_retreat_hint": "Retreat: attempt 1/1, chance {label}.",
    "chance_low": "low", "chance_medium": "medium", "chance_high": "high",
    "sum_enemy_attack": "{name} attacks: -{damage} hull.",
    "sum_enemy_ability": "{name} uses «{ability}»: -{damage} hull.",
    "sum_player_attack": "Your strike: -{damage} enemy hull.",
    "sum_victory": "Victory! +{xp} XP.{loot}",
    "sum_victory_loot": " Loot: {list}.",
    "sum_defeat": "Ship disabled — retreating to the spaceport.",
    "sum_retreat_fail": "Retreat failed — the enemy intercepts you!",
    "sum_retreat_ok": "You leave the battle. No victory reward.",
    "sum_retreat_damage": "Parting shot: -{damage} hull.",
    "sum_retreat_mal": "Malfunction gained.",
    "sum_use_heal": "Used {name}: +{healed} hull.",
    "sum_use_plain": "Used {name}.",
    "intro_fight": "⚔ Battle started: {name} [{category}]",
    "hp_player": "Player HP: {cur}/{max}",
    "hp_enemy": "Enemy HP: {cur}/{max}",
    "victory_title": "🏆 Victory!",
    "victory_reward": "Rewards: {list}",
    "victory_space": "Inventory space left: {free}/{size}",
    "defeat_title": "💥 Ship disabled. Wait for repairs (~{delay}s), then you'll automatically head to the spaceport.",
}

DEFAULT_LANG = "ru"


def t(key: str, lang: str | None = None, **kw: Any) -> str:
    """Шаблон по ключу и языку. EN fallback → RU → сам ключ."""
    table = EN if lang == "en" else RU
    template = table.get(key) or RU.get(key) or key
    return template.format(**kw) if kw else template

