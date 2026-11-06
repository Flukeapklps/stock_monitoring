#!/bin/sh
set -e

# Wait for PostgreSQL, then apply migrations (only the API container sets RUN_MIGRATIONS=true).
python - <<'PY'
import os, sys, time
import sqlalchemy as sa
url = os.environ.get("DATABASE_URL")
for i in range(60):
    try:
        sa.create_engine(url).connect().close()
        sys.exit(0)
    except Exception as exc:
        print(f"waiting for database ({type(exc).__name__})...", flush=True)
        time.sleep(2)
print("database not reachable", file=sys.stderr)
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "applying database migrations"
  alembic upgrade head
fi

exec "$@"
