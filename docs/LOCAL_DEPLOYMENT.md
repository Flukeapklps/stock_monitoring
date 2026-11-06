# Local Deployment

Requirements: Docker with Compose v2, ~3 GB disk, internet access to the data providers.

```bash
cp .env.example .env
```

Edit `.env`: set `ADMIN_PASSWORD`, a random `JWT_SECRET` (`openssl rand -hex 32`) and `SEC_USER_AGENT` with your
contact email. LINE and Alpha Vantage keys are optional.

```bash
docker compose up -d --build
```

Services: `postgres`, `redis` (internal only), `backend` (127.0.0.1:8000, runs migrations on start), `worker`
(scheduler), `frontend` (127.0.0.1:3000). Optional Nginx with production-like routing:
`docker compose --profile nginx up -d` → http://localhost:8080.

Load data (first run):

```bash
docker compose exec backend python -m app.cli load
```

```bash
docker compose exec backend python -m app.cli fundamentals
```

Open http://localhost:3000 (sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`). API docs: http://localhost:8000/api/docs.

Useful commands:

```bash
docker compose ps
```

```bash
docker compose logs -f backend worker
```

```bash
docker compose exec backend python -m app.cli daily
```

```bash
docker compose exec postgres psql -U stock -d stock
```

## Without Docker (development)

Backend (Python 3.11+, PostgreSQL 16, Redis):

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
cd backend && DATABASE_URL=postgresql+psycopg://stock:stock@localhost:5432/stock alembic upgrade head
```

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

```bash
cd backend && python -m app.worker
```

Frontend (Node 22): `/api` is proxied to `BACKEND_URL` (default http://localhost:8000).

```bash
cd frontend && npm ci && npm run dev
```

## Tests

```bash
docker run -d --name testpg -e POSTGRES_USER=stock -e POSTGRES_PASSWORD=stock -e POSTGRES_DB=stock_test -p 5432:5432 postgres:16-alpine
```

```bash
cd backend && pytest
```

```bash
cd frontend && npm test && npm run typecheck && npm run build
```

```bash
cd frontend && E2E_BASE_URL=http://localhost:3000 E2E_PASSWORD=<admin password> npx playwright test
```

The e2e suite needs a running stack with data loaded and makes many requests from one IP; set
`RATE_LIMIT_PER_MINUTE=1200` for that backend run.
