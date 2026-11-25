# Чек-лист готовности MVP (по критериям ТЗ)

Каждый критерий привязан к автотестам (tests/) и/или смоук-команде.
Стартовый баланс и контент v1 одобрены заказчиком 25.11.2026 (docs/04-decisions.md).

## Критерии готовности MVP (ТЗ §«Критерии готовности»)

| # | Критерий ТЗ | Статус | Проверка |
|---|---|---|---|
| 1 | Игрок создаёт персонажа, выбирает фракцию, проходит обучение и начинает экспедицию | ✅ | `test_characters.py`, `test_quests_db.py::test_tutorial_chain_progression`; смоук: start → launch |
| 2 | Экспедиция генерирует события, расходует топливо, добровольное/автоматическое возвращение | ✅ | `test_expeditions_db.py` (все 3 сценария возврата с reason-кодами) |
| 3 | Бой: ходы, способности, расходники, отступление, победа, поражение | ✅ | `test_battles_db.py` (11 тестов: порядок ходов, кулдауны способностей, лимит расходников, отступление 1/1) |
| 4 | Потери, награды, прочность и поломки без потери/дублирования предметов | ✅ | `test_hull.py` (−5%/поломка), `test_expeditions_db.py::test_defeat_from_hull_damage_loses_part_of_loot`, `test_battles_db.py::test_victory_grants_xp_and_loot_once` |
| 5 | Инвентарь, склад и раздел ожидания работают согласованно | ✅ | `test_inventory_db.py`, `test_crafting_db.py::test_waiting_auto_flushes_when_space_freed`, `test_store_frees_slots_and_flushes_waiting` |
| 6 | Модули: установка/снятие, развитие способностей и навыков | ✅ | `test_modules_db.py` (8 тестов), `test_progression.py` |
| 7 | Крафт: несколько таймеров, отмена, частичный возврат материалов | ✅ | `test_crafting_db.py` |
| 8 | Аукцион и прямая передача: комиссии, ограничения, резервы, защита от повторных операций | ✅ | `test_economy_db.py` (резервы ставок, возврат перебитых, идемпотентность webhook, подтверждения ценных) |
| 9 | Основные балансировочные параметры доступны через админ-панель | ✅ | `test_admin_db.py::test_config_hot_reload_applies`, HTTP-смоук PUT /admin/config |
| 10 | Мониторинг, аудит, резервное копирование, тестовая среда, инструкция развёртывания | ✅ | `/metrics` + `app/metrics.py`; `audit_log` (тест `test_audit_log_written`); `tools/backup.sh` + `restore.sh` (проверено восстановление); CI-матрица; `docs/runbook.md` |

## Покрытие критических разграничений ТЗ

| Разграничение | Где проверяется |
|---|---|
| Прочность = здоровье; поломки режут максимум на 5%; ремкомплект ≠ хил | `test_hull.py`, `test_battles_db.py::test_non_consumable_item_rejected_in_combat` |
| Прямой дроп при полном инвентаре теряется; результат таймера → «Ожидает получения» | `test_inventory_db.py::test_inventory_full_raises_unless_allow_loss`, `test_crafting_db.py::test_craft_result_goes_to_waiting_when_full` |
| Три сценария возвращения — разные ветки | `test_expeditions_db.py`: voluntary / fuel_empty / defeat |
| Запрет продажи и запрета передачи — независимые флаги | модель `ItemTemplate.sellable/tradable`; `economy.create_transfer` (tradable), `market.create_lot` (sellable) |
| Модуль не требует изучения; опыт распределяется | `test_modules_db.py::test_faction_starting_module_granted`, `train` |

## Нагрузка (этап 10)

- 50 параллельных игроков, 1000 команд: **246 cmd/s, 100% ok, 0 занято, 0 ошибок**
  (локальная БД, `python -m tools.load_test`). Узкое место — per-character lock,
  при штатном паттерне «игрок шлёт команды по очереди» конфликтов нет.

## Открыто до production (только с заказчиком — docs/04-decisions.md §B)

1. Платёжный провайдер + цены паков Эонов в рублях (деньги/договор).
2. Production-инфраструктура: VPS/домен.
3. Secure key VK-приложения для продовой аутентификации Mini App.
4. Telegram-адаптер — по желанию (нужен токен бота).
