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
- **i18n**: `app/locale.py` (RU активен, EN готов). Команды НЕ переводятся.
  Кнопки VK: label локализован, команда — в payload (бот читает payload).
  Язык игрока: `characters.lang` (переключателя пока нет, дефолт ru).
- **Конфиг баланса** — всё в `config_params` (правится из админ-панели, hot reload).
  Админка: http://localhost:8000/admin-panel (веб-UI), ключ X-Admin-Key, роли
  admin/moderator/content_manager, аудит в `audit_log`.

## Ключевые правила ТЗ (не сломать!)

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
   Раскладка экспедиции: [Исследовать][Залатать корабль] / [Инфо][В космопорт].
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
