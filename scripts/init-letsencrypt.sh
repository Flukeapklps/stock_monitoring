#!/usr/bin/env bash
# Obtain the first Let's Encrypt certificate (HTTP-01 via webroot).
# Usage: LETSENCRYPT_EMAIL=you@example.com scripts/init-letsencrypt.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${DOMAIN:?set DOMAIN in .env}" "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
# Temporary HTTP-only nginx to answer the ACME challenge
docker volume create stock-monitor_certbot-webroot >/dev/null
docker run -d --rm --name acme-nginx -p 80:80 -v stock-monitor_certbot-webroot:/usr/share/nginx/html:ro nginx:1.27-alpine >/dev/null
trap 'docker stop acme-nginx >/dev/null 2>&1 || true' EXIT
$COMPOSE --profile certbot run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
  -d "$DOMAIN" --email "$LETSENCRYPT_EMAIL" --agree-tos --no-eff-email --non-interactive
echo "Certificate issued for $DOMAIN. Now run scripts/deploy.sh"
