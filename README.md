# Текстовая космическая MMORPG — MVP

Архитектура, модель данных и план этапов: см. `docs/01-architecture.md`, `docs/02-data-model.md`, `docs/03-roadmap.md`, `docs/04-decisions.md`.

## Быстрый старт (dev на Windows)

```bash
# 1. Окружение
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt

# 2. PostgreSQL 16+ (по умолчанию ожидается):
#    postgresql://postgres:postgres@localhost:5432/postgres
#    иначе задайте DATABASE_URL

# 3. Миграции
.venv/Scripts/python -m alembic upgrade head

# 4. Проверки
.venv/Scripts/python -m pytest -q            # unit без БД; integration — при TEST_DATABASE_URL
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy app core worker adapters tests

# 5. Запуск
.venv/Scripts/python -m uvicorn adapters.http.app_factory:create_app_from_settings --factory --port 8000
.venv/Scripts/python -m worker.main                       # планировщик + outbox
.venv/Scripts/python -m app.seeds                         # контент-заглушки (фракции, предметы)
.venv/Scripts/python -m adapters.console                  # консольный бот (dev)

# VK-бот (нужны токен сообщества и id группы):
setx VK_BOT_TOKEN "..." && setx VK_GROUP_ID "123456"
.venv/Scripts/python -m adapters.vk.bot

## Команды игрока (этапы 1–8)

```
factions                     — список фракций
start <имя>                  — запрос выбора фракции
start <имя> <код_фракции>    — создание персонажа
me / inv [ship|warehouse|waiting] / ship   — профиль, инвентарь, корабль
launch                       — старт экспедиции (космопорт → космос)
go                           — идти дальше: топливо −цена события, событие открывается
exp                          — статус экспедиции
choice <n>                   — выбор в интерактивном событии
decline                      — отказаться от события (если разрешён типом)
return                       — добровольное возвращение (вне события/боя)
launch                       — старт экспедиции (космопорт → космос)
attack                       — удар по противнику (бой: игрок, затем враг)
use <код>                    — боевой расходник (например: use nano_patch)
retreat                      — отступление (1 попытка за бой)
battle / blog                — статус боя / подробный боевой журнал
mods                         — модули (способности/навыки) и опыт
install <код> / uninstall <код> — установка/снятие (только в космопорту)
train <код> <опыт>           — вложить опыт из пула в модуль (только в космопорту)
resetbuild                   — сброс распределения опыта (спец. ресурс, растёт по формуле)
<команда способности>        — например shot: активная способность установленного модуля
recipes / craft <код>        — рецепты / запуск крафта (только в космопорту)
crafting / craftcancel <n>   — активные заказы / отмена (частичный возврат материалов)
store <код> <n> / take <код> <n> — склад ↔ инвентарь (только в космопорту)
wallet                       — Эоны и Кванты (баланс/резервы)
send <имя> <кванты>          — передача Квантов (ценные — с подтверждением получателя)
senditem <имя> <код> <n>     — передача предметов
inbox / confirm <id> / decline <id> — входящие предложения передач
sell <код> <n> <цена> [выкуп] — лот на аукцион; lots / bid <id> <цена> / lotcancel <id>
market [buy|sell <эоны> <цена>] — внутренний рынок Эонов (сведение — worker)
shop / buy <код>             — магазин (подписки 7/30 дней за Эоны)
quests                       — активные задания и фракционные события
```

Экономика: изменение балансов только через append-only `wallet_ledger` в той же
транзакции; до подтверждения активы зарезервированы; повторный платёжный webhook
не дублирует зачисление (UNIQUE provider+payment_id).

## Admin API (этап 9)

Аутентификация — заголовок `X-Admin-Key` (хеш в БД; владелец засеян миграцией,
**сменить ключ перед продом**). Все действия пишутся в `audit_log` с diff.

| Роль | Возможности |
|---|---|
| `admin` | конфиги/баланс (hot reload), контент, игроки, экономика, аналитика |
| `moderator` | бан/разбан, применение существующих эффектов (белый список), история |
| `content_manager` | публикация контента только в своей зоне (события/задания/рецепты) |

```
GET/PUT /admin/config/{key}          — баланс без кода и перезапуска (ТЗ)
POST /admin/accounts/{id}/ban        — {reason, hours?}
POST /admin/accounts/{id}/unban
POST /admin/characters/{id}/effects  — {code, params?, duration_sec?}
POST /admin/content/{table}/{code}/status — {status: draft|published|archived}
GET /admin/analytics                 — игроки, активность за сутки, воронка обучения
```

Mini App API (`/api/*`): dev-режим — заголовок `X-Character-Id` (в prod
отключается до верификации подписанных launch params VK — решение C2):
`GET /api/me`, `/api/inventory?kind=`, `/api/mods`, `/api/battles/{id}/log`.

## Эксплуатация (этап 10)

- **Метрики**: `GET /metrics` (Prometheus) — команды по результатам
  (ok/domain_error/busy/error), латентность, outbox-ошибки, таймеры.
- **Бэкап/восстановление**: `tools/backup.sh` (pg_dump + ротация 14),
  `tools/restore.sh` (restore в отдельную БД + проверка целостности) — проверено.
- **Нагрузочный тест**: `python -m tools.load_test --characters 50 --commands 20`
  → 246 cmd/s, 100% ok (локально).
- **Runbook**: `docs/runbook.md` — деплой, миграции, откат, maintenance-режим,
  алерты, секреты.
- **Чек-лист готовности MVP**: `docs/acceptance.md` — все 10 критериев ТЗ
  привязаны к автотестам.

Крафт: таймеры идут в реальном времени (и офлайн), результат не поместившийся
в инвентарь уходит в «Ожидает получения» и возвращается автоматически при
освобождении места. Завершения группируются в одно уведомление, важные
результаты (rare+) — отдельно.

Сценарии возвращения (ТЗ): добровольное (без штрафа, топливо сохраняется),
исчерпание топлива (автовозврат + поломки), поражение (потеря доли добычи,
только предметы с флагом потери; редкие/уникальные защищены). Прибытие —
таймер worker'а, сообщения о начале и прибытии приходят через outbox.
Экспедиция офлайн не тикает: топливо не тратится, события не запускаются.
```

Интеграционные тесты (конвейер команд, outbox, таймеры) требуют тестовую БД:

```bash
setx TEST_DATABASE_URL "postgresql+asyncpg://postgres:postgres@localhost:5432/space_mmo_test"
pytest -q -m integration
```

## Docker

```bash
docker compose -f deploy/docker-compose.yml up -d db        # только PostgreSQL
docker compose -f deploy/docker-compose.yml --profile app up # + api и worker
```

## Структура

```
app/        слой приложения: Command Bus, конфигурация, модели, хранилища
core/       игровое ядро (чистые правила, без БД/HTTP/платформ)
adapters/   vk (этап 1), console (dev), http (Mini App API)
worker/     планировщик таймеров, диспетчер outbox (лидерство через advisory lock)
alembic/    миграции
tests/      unit (без БД) + integration (TEST_DATABASE_URL)
deploy/     Dockerfile, docker-compose.yml
```

## Гарантии конвейера (ТЗ «Надёжность»)

- Команды персонажа выполняются последовательно: `pg_try_advisory_xact_lock`, иначе «занят».
- Идемпотентность: `idempotency_keys`, повтор возвращает сохранённый ответ без эффектов.
- Атомарность: use case + outbox + ключ в одной транзакции; платформа недоступна —
  сообщения уходят из outbox позже (retry с бэкоффом), эффект применён ровно один раз.
- Таймеры (возвращение, крафт, лоты) работают офлайн через таблицу `timers` + worker-лидер.

## Резервное копирование и откат

```bash
pg_dump -Fc -U postgres space_mmo > backup_$(date +%F).dump   # перед миграциями и по расписанию
pg_restore -U postgres -d space_mmo_backup backup.dump        # проверка восстановления
alembic downgrade -1                                          # откат миграции (после бэкапа)
```

Режим обслуживания: `UPDATE maintenance_state SET enabled = true;` — бот отвечает заглушкой
(проверка подключается на этапе 1 вместе с обработчиком команд).
