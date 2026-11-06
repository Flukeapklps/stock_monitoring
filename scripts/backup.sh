#!/usr/bin/env bash
# PostgreSQL backup: compressed pg_dump (custom format) with retention.
# Usage: scripts/backup.sh [backup_dir]     (cron example: 30 2 * * * /opt/stock-monitor/scripts/backup.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
BACKUP_DIR="${1:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE="docker compose -f docker-compose.yml"
[ -f docker-compose.prod.yml ] && [ "${ENVIRONMENT:-production}" = "production" ] && COMPOSE="$COMPOSE -f docker-compose.prod.yml"
set -a; [ -f .env ] && . ./.env; set +a
mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/stock-${TS}.dump"
echo "Creating backup $FILE"
$COMPOSE exec -T postgres pg_dump -U "${POSTGRES_USER:-stock}" -d "${POSTGRES_DB:-stock}" -Fc --no-owner > "$FILE"
# verify the archive is readable
$COMPOSE exec -T postgres pg_restore --list < "$FILE" > /dev/null
sha256sum "$FILE" > "$FILE.sha256"
find "$BACKUP_DIR" -name 'stock-*.dump*' -mtime +"$RETENTION_DAYS" -delete
echo "Backup complete: $(du -h "$FILE" | cut -f1)"
