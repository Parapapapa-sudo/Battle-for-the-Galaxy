#!/usr/bin/env bash
# Резервное копирование PostgreSQL: pg_dump в каталог с ротацией (ТЗ).
#
# Использование:
#   PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres PGPASSWORD=... \
#   BACKUP_DB=space_mmo BACKUP_DIR=./backups ./tools/backup.sh
#
# Ротация: хранится последние KEEP копий (по умолчанию 14).
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
BACKUP_DB="${BACKUP_DB:-space_mmo}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP="${KEEP:-14}"
PG_BIN="${PG_BIN:-/c/Program Files/PostgreSQL/17/bin}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
FILE="$BACKUP_DIR/${BACKUP_DB}_${STAMP}.dump"

"$PG_BIN/pg_dump.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$BACKUP_DB" \
    -Fc --no-owner --no-privileges -f "$FILE"

# ротация: оставляем KEEP последних
ls -1t "$BACKUP_DIR"/${BACKUP_DB}_*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "backup_ok: $FILE ($(du -h "$FILE" | cut -f1))"
