#!/usr/bin/env bash
# Local development convenience: create .env (if missing), start the stack, load initial data.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i.bak "s/^JWT_SECRET=.*/JWT_SECRET=$(openssl rand -hex 32)/" .env && rm -f .env.bak
  echo "Created .env (edit ADMIN_PASSWORD, LINE_* and provider keys as needed)"
fi
docker compose up -d --build
echo "Loading initial history for the default universe (this downloads ~10y of daily data)..."
docker compose exec backend python -m app.cli load
docker compose exec backend python -m app.cli fundamentals || true
echo "Dashboard: http://localhost:3000   API docs: http://localhost:8000/api/docs"
