#!/usr/bin/env bash
# Restore a backup created by backup.sh. DESTRUCTIVE: replaces the current database.
# Usage: scripts/restore.sh backups/stock-YYYYmmddTHHMMSSZ.dump
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: restore.sh <backup.dump>}"
[ -f "$FILE" ] || { echo "no such file: $FILE"; exit 1; }
if [ -f "$FILE.sha256" ]; then sha256sum -c "$FILE.sha256"; fi
set -a; [ -f .env ] && . ./.env; set +a
COMPOSE="docker compose -f docker-compose.yml"
[ -f docker-compose.prod.yml ] && [ "${ENVIRONMENT:-production}" = "production" ] && COMPOSE="$COMPOSE -f docker-compose.prod.yml"
DB="${POSTGRES_DB:-stock}"; USER_="${POSTGRES_USER:-stock}"
read -r -p "This will REPLACE database '$DB' with $FILE. Type 'restore' to continue: " ok
[ "$ok" = "restore" ] || { echo "aborted"; exit 1; }
echo "Stopping application containers"
$COMPOSE stop backend worker frontend || true
$COMPOSE exec -T postgres psql -U "$USER_" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$DB\";" -c "CREATE DATABASE \"$DB\" OWNER \"$USER_\";"
$COMPOSE exec -T postgres pg_restore -U "$USER_" -d "$DB" --no-owner --exit-on-error < "$FILE"
echo "Starting application (migrations run automatically)"
$COMPOSE up -d backend worker frontend
echo "Restore complete"
