# CONTEXT — контекст разработки (для нового ИИ-агента)

Если ты подключаешься к проекту без истории диалога — начни отсюда. Проект:
**текстовая космическая MMORPG в чате VK («Battle for the Galaxy»), MVP**.
ТЗ: `docs/` (спека принята, баланс v1 одобрен заказчиком 25.11.2026).

## Состояние (на 25.11.2026)

Все этапы MVP (0–10) реализованы и покрыты тестами: **157 тестов зелёные**,
ruff и mypy strict чистые. Работает в VK-группе 243684200 (бот + кнопки).
Платёжный контур готов на мок-провайдере (реальный API ждём). Mini App
отложен до VPS (VK требует публичный URL).

## Запуск (Windows, локально)

```bash
cd D:\new
# .venv уже создан (Python 3.12). Если нет: py -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
# PostgreSQL 17 (сервис postgresql-x64-17, пароль postgres), БД dev: postgres, тестовая: space_mmo_test
.venv/Scripts/python -m alembic upgrade head          # миграции 0001–0011
.venv/Scripts/python -m app.seeds                     # контент-заглушки (идемпотентно)
.venv/Scripts/python -m adapters.vk.bot               # бот (токены в .env)
.venv/Scripts/python -m worker.main                   # таймеры/крафт/прибытия (отдельное окно!)
.venv/Scripts/python -m uvicorn adapters.http.app_factory:create_app_from_settings --factory --port 8000
```

- `.env` (в .gitignore): DATABASE_URL, VK_BOT_TOKEN, VK_GROUP_ID, VK_APP_SECRET
  (защищённый ключ Mini App), VK_SERVICE_TOKEN, APP_ENV=dev. Ключи админки:
  owner=`dev-only-owner-key`, moderator/content_manager — выданы заказчику.
- **Важно**: без worker'а прибытия/крафт «зависают» — но роутер делает auto-sync
  таймеров при каждой команде игрока (`route_text_command` → `sync_character_timers`).
  Ручная команда игрока: `sync`.

## Проверки

```bash
.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m mypy app core worker adapters tests
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/space_mmo_test .venv/Scripts/python -m pytest -q
```
Тестовая БД пересоздаётся перед важными прогонами (dropdb/createdb). Интеграционные
тесты используют rollback-фикстуру (tests/conftest.py); HTTP-тесты коммитят и
чистятся TRUNCATE (фикстура http_seeded). **Не оставляй висячих python-процессов**
(блокируют dropdb): `taskkill //F //IM python.exe`.

## Архитектура (коротко)

- Слои: `core/` чистая логика (без БД) → `app/` use cases + модели SQLAlchemy →
  `adapters/` (vk бот+клавиатуры, http API: miniapp/admin/payments/mock-страница,
  console) → `worker/` (лидер через advisory lock: таймеры, outbox, сведение рынка Эонов).
- **CommandBus** (`app/bus.py`): per-character advisory lock (последовательность),
  идемпотентность (`idempotency_keys`), outbox в той же транзакции, maintenance-флаг.
- **Деньги**: только через `app/economy.py` (ledger + резервы). **Инвентарь**:
  `grant_items/remove_items/flush_waiting` (waiting = безлимитный, авто-возврат
  при освобождении места). Прямой дроп при полном инвентаре теряется, результат
  таймера — никогда.
- **Таймеры**: единая таблица `timers` + реестр обработчиков `app/timer_handlers.py`
  (общий для worker и sync).
- **Анти-спам (критично!)**: бот игнорирует message_new старше STALE_UPDATE_SEC=120с
  (`adapters/vk/bot.py`) — бэклог long poll (реконнекты, второй поллер со старым ts,
  повторные доставки VK) иначе исполняет старые кнопки спустя часы («игра играет сама»,
  рассылка всем игрокам). Идемпотентность по ключу vk:<user>:<cmid> от повторных
  ЭФФЕКТОВ защищает, но не от первого исполнения старого сообщения.
- **Outbox доставляют ОБА процесса** (бот-pump и worker) — claim атомарен (SKIP LOCKED).
  Worker ОБЯЗАН использовать VkSender (`worker/main.py::_sender_from_settings`);
  PrintSender допустим только без VK-конфига — иначе «съедает» сообщения
  (помечает sent, игрок ничего не получает).
- **i18n**: `app/locale.py` (RU активен, EN готов). Команды НЕ переводятся.
  Кнопки VK: label локализован, команда — в payload (бот читает payload).
  Язык игрока: `characters.lang` (переключателя пока нет, дефолт ru).
- **Команда `ui`** (алиас «меню», аккаунт-уровень — работает и без персонажа):
  перезагрузка интерфейса. Каждый ответ бота несёт актуальную клавиатуру,
  поэтому ответ «Меню обновлено.» возвращает кнопки, если они не подгрузились.
- **Онбординг** (accounts.onboarding JSONB, миграция 0016): приветствие
  (welcome_new + кнопка «Начать»=intro) → обзор игры (intro_overview, кнопка
  «Продолжить»=intro2) → предыстория + кнопки фракций (join <code>) →
  ПРОСМОТР фракции (step=preview, выбор НЕ зафиксирован; кнопки «Выбрать эту
  фракцию»=pick <code> и «◀ К списку») → pick фиксирует фракцию и просит имя →
  СВОБОДНЫЙ текст = имя капитана (роутер создаёт персонажа, при ошибке
  валидации состояние сохраняется). Классический «start <имя> <фракция>»
  работает; «start»/«начать» без аргументов = intro. Клавиатуры шагов —
  `keyboards.keyboard_for_account`. Текст start_created подсказывает первый
  шаг («Экспедиция» + Штаб→Задания).
- **Квесты: принятие и отслеживание** (миграция 0019): задания цепочки
  появляются как AVAILABLE (CharacterQuest.status='available'), игрок принимает
  кнопкой «Принять» в Штабе (accept <code>) — задание становится active и
  ОТСЛЕЖИВАЕМЫМ (characters.tracked_quest). Прогресс отслеживаемого приходит
  в чат при каждом изменении (📜 «имя»: цель x/y, outbox из quests.track).
  track <code> — сменить отслеживаемое (только активное). Завершение снимает
  отслеживание, следующее задание цепочки снова available. Прогресс
  засчитывается ТОЛЬКО активным заданиям.
- **Картинки к сообщениям** (`app/images.py` + `adapters/vk/images.py`): паки —
  папки `assets/images/<пак>/` (jpg/jpeg/png/gif, пополняются копированием
  файлов). Паки: travel (путь/вылет/«Инфо» в пути), battle (вступление в бой),
  port_main / port_workshop / port_hq (залы, прибытие). CommandResult.image_pack
  — для прямых ответов; outbox — payload {"image_pack": ...} (прибытие, бой из
  автособытия). VK-загрузка однократная: attachment кэшируется в config_params
  `images.cache` (инвалидация по mtime файла). Пустой пак/сбой загрузки — ответ
  уходит без фото, ничего не ломается.
- **Залы космопорта** (`characters.hall`: main|workshop|hq, команда `hall <зал>`,
  только в порту): основной [Экспедиция][О игроке]/[Мастерская][Штаб];
  мастерская [Модули][Крафт]/[Инвентарь][О корабле]/[В основной зал];
  штаб [Задания][Магазин]/[В основной зал]. Прибытие из экспедиции сбрасывает
  зал в main. Кнопка wallet из меню убрана (команда wallet работает).
- **Конфиг баланса** — всё в `config_params` (правится из админ-панели, hot reload).
  Админка: http://localhost:8000/admin-panel (веб-UI), ключ X-Admin-Key, роли
  admin/moderator/content_manager, аудит в `audit_log`.
- **Редактор предметов** (вкладка «Предметы», роль admin): создание/изменение
  item_templates — название, тип (category), редкость, уникальность, флаги
  sellable/tradable/losable_on_defeat, лимит стека, params JSON и БОНУСНЫЙ ДРОП:
  `drop_weight` (% шанс после события экспедиции, 0 = не падает) + диапазон
  `drop_qty_min/max`. Дроп бросается в `expeditions._roll_expedition_drop` после
  каждого НЕбоевого исхода события (у врагов — свои loot_tables). API:
  GET/POST /admin/items, PUT /admin/items/{code}.
- **Аналитика админки** (вкладка «Аналитика», разделы раскрываются «Показать»):
  нагрузка на сервер (CPU/память процесса API через stdlib — psutil НЕТ, БД,
  очереди timers/outbox — `admin.server_load`, GET /admin/server-load);
  игровой мир человекочитаемо (`admin.analytics`); журнал аудита — время МСК,
  ключ, краткая суть (`admin.audit_log_view`, GET /admin/audit-log).
- **Инструменты** (вкладка «Инструменты», `app/health.py`): проверка VK (вызов
  users.get с токеном), TG (getMe при заданном TG_BOT_TOKEN, иначе доступность
  api.telegram.org), сервер (БД + живость бота/worker по advisory-локам в
  pg_locks, скоуп по текущей БД!). Ручной запуск: POST /admin/health/check
  {target: vk|tg|server|all}. Расписание: PUT /admin/health/schedule
  {interval_sec} (0 = выкл, минимум 30с), выполняет worker (`_health_tick`).
  Результаты и расписание — в config_params (`tools.health_last`,
  `tools.health_interval_sec`), UI показывает время последней проверки МСК.
- **Редактор контента** (вкладка «Контент», роль admin+content_manager):
  события/задания/рецепты/КОРАБЛИ — список, создание, правка, удаление
  (GET/POST /admin/content[/{kind}], PUT/DELETE /admin/content/{kind}/{code}).
  Валидация per-kind в `admin._validate_content` (interactive → script.choices,
  automatic → script.variants; рецепт → result_item_code существует; корабль →
  характеристики в base_stats, фракция по коду, статуса у корабля НЕТ).
  Удаление: жёсткое, если нет ссылок; события/задания в истории игроков —
  status='archived'; корабль, которым владеют игроки (Ship RESTRICT), —
  ошибка «используется игроками» (архива у кораблей нет).
  JSON-поля (script/objectives/rewards/materials) редактируются как JSON-текст.
- **Секции конфигурации** (вкладка «Конфигурация», реестр `admin.CONFIG_SECTIONS`):
  character / expedition / combat / economy + factions (прибавки из
  factions.bonuses, ключи «<faction>.<bonus>», подписи FACTION_BONUS_LABELS).
  GET /admin/config-sections, PUT /admin/config-sections/{id} — сохранение
  вкладки целиком с валидацией (типы/диапазоны/пары «от-до» _SECTION_PAIRS);
  запись через set_config (hot reload, аудит каждого ключа). Новый параметр
  в UI = строка в CONFIG_SECTIONS.

## Ключевые правила ТЗ (не сломать!)

0. **Лор фракций (канон заказчика, миграция 0015)**: azimuth = «Вольная Лига»
   (торговля), helion = «Астрийский Конкордат» (военная империя), cinder =
   «Пепельный Предел» (пираты), lumen = «Синод Эонов» (знания). Коды НЕ менять —
   это идентификаторы в БД и командах; названия/описания — в seeds + миграции.

1. Последовательность команд игрока (lock), занятость → «Команда выполняется».
2. Идемпотентность всего (повтор webhook платежа/команды не дублирует эффект).
3. Три сценария возвращения: voluntary (топливо сохраняется) / fuel_empty (+поломки) /
   defeat (потеря 10–30% добычи, только losable_on_defeat). Задержка возврата 30с.
4. Путешествие между событиями: «Исследовать» НЕ создаёт событие сразу — корабль
   уходит в путь на 10с + (базовое топливо − текущее)/2 (`expedition.next_event_at`,
   config expedition.event_cooldown_base_sec). Событие прибывает таймером
   `exp_event` → `expeditions.arrive_event` (уведомление через outbox).
   В пути доступна только кнопка «Инфо»; return/patch бросают DomainError("traveling");
   повторный go возвращает тот же экран пути (без дублей таймеров).
   Экран пути: текст + индикатор топлива ■/□ (как HP, `describe_travel`).
   Раскладка экспедиции: [Исследовать][Залатать] / [Инфо][В космопорт].
   Во время ремонта («Залатать») — тоже только «Инфо»; ремонт считается идущим,
   пока patch_eta не снят patch_complete (строгая проверка — защита от двойного
   таймера и потери уведомления patch_done).
   После КАЖДОГО завершения события (исход/выбор/отказ) игроку уходит отдельное
   сообщение о состоянии: прочность (■/□), поломки, свободные слоты инвентаря
   (`describe_ship_status`, outbox — приходит после описания события).
5. Бой: порядок игрок→враг, без автохода; отступление 1/1; опыт только за победу.
6. Экспедиция офлайн не тикает; таймеры (крафт/прибытие/лоты) — реальное время.
7. Уникальные предметы: не продать (sellable=False), sellable/tradable — независимы.
8. Эоны: mint только за оплату, burn только за покупки; 1 Эон = 1 ₽ (eon.rub_rate).

## Открыто (только заказчик)

- B1 платёжный провайдер (контур готов: `adapters/payments/base.py` — реализовать
  протокол, `provider_from_settings`; webhook `/payments/webhook` идемпотентен).
- B2 цены паков Эонов в рублях. B3 VPS/домен (после — Mini App: страница `/app`
  готова, верификация launch params через VK_APP_SECRET работает).
- Переключатель языка игрока (EN таблица готова, UI переключения нет).

## Соглашения

- mypy strict + ruff; комментарии только на русском, по делу. Миграции — ручные,
  явные (alembic/versions/00NN_*.py), без autogenerate.
- Тесты пишем под любое новое правило ТЗ; числа баланса — в config_params, не в коде.
- Перед push: ruff, mypy, полный pytest. Коммиты на русском, атомарные.
