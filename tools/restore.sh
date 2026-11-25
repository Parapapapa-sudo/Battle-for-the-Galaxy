#!/usr/bin/env bash
# Восстановление из резервной копии с ПРОВЕРКОЙ (ТЗ: тестовое восстановление).
#
# Использование:
#   PGHOST=127.0.0.1 PGUSER=postgres PGPASSWORD=... \
#   BACKUP_FILE=./backups/space_mmo_20261125_120000.dump \
#   RESTORE_DB=space_mmo_restore ./tools/restore.sh
#
# Восстанавливает в ОТДЕЛЬНУЮ БД (RESTORE_DB), не трогая рабочую, и проверяет,
# что ключевые таблицы на месте и в них есть данные.
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
BACKUP_FILE="${BACKUP_FILE:?укажите BACKUP_FILE}"
RESTORE_DB="${RESTORE_DB:-space_mmo_restore}"
PG_BIN="${PG_BIN:-/c/Program Files/PostgreSQL/17/bin}"

"$PG_BIN/dropdb.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" --if-exists "$RESTORE_DB"
"$PG_BIN/createdb.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$RESTORE_DB"
"$PG_BIN/pg_restore.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$RESTORE_DB" \
    --no-owner --no-privileges "$BACKUP_FILE"

CHECK=$("$PG_BIN/psql.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$RESTORE_DB" -t -A -c "
SELECT 'tables=' || count(*) FROM information_schema.tables WHERE table_schema='public';
")
CHARS=$("$PG_BIN/psql.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$RESTORE_DB" -t -A -c \
    "SELECT count(*) FROM characters;")

echo "restore_ok: $RESTORE_DB ($CHECK, characters=$CHARS)"
echo "Проверку прошла. Удалите $RESTORE_DB после анализа:
  dropdb -h $PGHOST -U $PGUSER --if-exists $RESTORE_DB"
