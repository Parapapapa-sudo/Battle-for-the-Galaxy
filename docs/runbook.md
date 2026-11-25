# Runbook — эксплуатация MVP

## Архитектура деплоя

Компоненты (Docker Compose, `deploy/docker-compose.yml`):
- `db` — PostgreSQL 16 (единственный источник истины; advisory locks, JSONB).
- `api` — FastAPI: Mini App API, Admin API, `/healthz`, `/metrics`, `/payments/webhook`.
- `worker` — планировщик таймеров + диспетчер outbox + сведение рынка Эонов
  (лидерство через advisory lock — безопасен в 1 экземпляре; масштабирование — позже).
- `vk-adapter` — запускается отдельно: `python -m adapters.vk.bot`
  (нужны `VK_BOT_TOKEN`, `VK_GROUP_ID`).

## Развёртывание (мелкое обновление, без остановки)

1. `git pull && docker compose build api worker`
2. `docker compose up -d api worker` — rolling restart; per-character advisory locks
   гарантируют отсутствие «полураспределённых» команд (транзакция либо применена, либо нет).
3. Проверка: `GET /healthz` → `{"status":"ok"}`, `GET /metrics`.

## Крупное обновление (режим обслуживания)

1. Включить заглушку: `UPDATE maintenance_state SET enabled = true;`
   (команды отклоняются с «Технические работы» — проверка в CommandBus).
2. Дождаться завершения активных команд (секунды) и outbox-доставки.
3. **Бэкап** (обязателен перед миграциями):
   `PGPASSWORD=... BACKUP_DB=space_mmo ./tools/backup.sh`
4. `docker compose up -d --build` + `alembic upgrade head` (в контейнере api/worker).
5. Выключить заглушку: `UPDATE maintenance_state SET enabled = false;`

## Откат

- **Код** (миграции не меняли схему): redeploy предыдущего образа.
- **Миграции**: `alembic downgrade -1` (после бэкапа). Проверка отката — часть релизного чек-листа.
- **Данные**: восстановление из дампа — см. ниже.

## Резервное копирование и восстановление

- Ежедневно (cron): `PGPASSWORD=... BACKUP_DB=space_mmo BACKUP_DIR=/srv/backups ./tools/backup.sh`
  (pg_dump custom-формат, ротация — 14 копий).
- **Проверка восстановления — ежемесячно** (ТЗ):
  `BACKUP_FILE=/srv/backups/space_mmo_....dump RESTORE_DB=space_mmo_restore ./tools/restore.sh`
  Скрипт восстанавливает в отдельную БД и проверяет целостность (таблицы + число персонажей).
- Аварийное восстановление: остановить приложение → `restore.sh` с `RESTORE_DB=space_mmo`
  (предварительно `dropdb space_mmo`) → поднять приложение.

## Мониторинг и алерты

- Метрики: `GET /metrics` (Prometheus). Ключевые: `mmo_commands_total`
  (result: ok|domain_error|busy|error), `mmo_command_seconds` (p95 латентность),
  `mmo_outbox_attempts_failed_total` (рост → проблема с платформой),
  `mmo_timer_fires_total`.
- Дашборд Grafana: RPS по `mmo_commands_total`, доля error>1% — алерт,
  outbox-failed>10/мин — алерт, лаг таймеров (разница now - max(fires_at fired)).
- Логи: JSON в stdout (structlog), correlation id через contextvars.
- Доступность: `GET /healthz` (`degraded` при недоступности БД) — внешний аптайм-чек.

## Эксплуатация worker'а

- Worker лидер-выборки через advisory lock: два экземпляра не навредят, но
  таймеры исполняет только лидер. Проверка лидерства: лог `worker_leader_acquired`.
- Пропуск таймеров недопустим: алерт на глубину очереди
  `SELECT count(*) FROM timers WHERE status='pending' AND fires_at < now() - interval '5 minutes'`.

## Секреты и окружения

- `DATABASE_URL`, `VK_BOT_TOKEN`, `VK_GROUP_ID`, `ADMIN_API_KEY` (сменить дефолтный
  ключ владельца — см. миграцию 0009) — через env/vault, не в репозитории.
- Окружения: dev (локально), staging (отдельная группа VK, без реальных платежей),
  prod. Платёжный webhook в staging — мок-провайдер.
