#!/usr/bin/env bash
# Production deploy / update on a Linux VPS.
# Usage: scripts/deploy.sh            (run from the repository root on the server)
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
[ -f .env ] || { echo ".env missing: cp .env.example .env and fill it in"; exit 1; }
set -a; . ./.env; set +a
for v in POSTGRES_PASSWORD JWT_SECRET DOMAIN ADMIN_EMAIL ADMIN_PASSWORD; do
  [ -n "${!v:-}" ] || { echo "$v must be set in .env"; exit 1; }
done
[ "${#JWT_SECRET}" -ge 32 ] || { echo "JWT_SECRET must be at least 32 characters"; exit 1; }
mkdir -p logs/backend logs/worker logs/nginx backups

if [ -d .git ]; then git pull --ff-only; fi
if docker compose ps postgres --status running -q 2>/dev/null | grep -q .; then
  echo "Backing up before deploy"; ENVIRONMENT=production scripts/backup.sh || echo "warning: pre-deploy backup failed"
fi
$COMPOSE build --pull
$COMPOSE up -d postgres redis
$COMPOSE up -d backend worker frontend
if ! $COMPOSE run --rm --no-deps --entrypoint "" nginx test -d "/etc/letsencrypt/live/$DOMAIN" 2>/dev/null; then
  echo "No certificate yet for $DOMAIN: run scripts/init-letsencrypt.sh first"; exit 1
fi
$COMPOSE up -d nginx
$COMPOSE --profile certbot up -d certbot
echo "Waiting for health check"
for i in $(seq 1 30); do
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then echo "Deployed: https://$DOMAIN"; exit 0; fi
  sleep 5
done
echo "Health check failed; inspect: $COMPOSE logs --tail=200 backend nginx"; exit 1
