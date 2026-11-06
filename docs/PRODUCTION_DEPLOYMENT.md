# Production Deployment (Linux VPS / Ubuntu / cloud VM)

Target: Ubuntu 22.04/24.04, 2 vCPU, 4 GB RAM, 20 GB disk, a DNS A record for your domain, ports 80/443 open.

## 1. Server preparation

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git ufw
```

```bash
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER
```

```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
```

Note: Docker-published ports bypass UFW; in production only Nginx publishes ports (80/443). PostgreSQL, Redis,
the API and the frontend stay on the internal Docker network.

## 2. Configure

```bash
sudo mkdir -p /opt/stock-monitor && sudo chown $USER /opt/stock-monitor && git clone <repo-url> /opt/stock-monitor
```

```bash
cd /opt/stock-monitor && cp .env.example .env && chmod 600 .env
```

Set at least: `ENVIRONMENT=production`, `DOMAIN`, `PUBLIC_BASE_URL=https://<domain>`, `CORS_ORIGINS=https://<domain>`,
`POSTGRES_PASSWORD` (strong), `JWT_SECRET` (≥32 chars, `openssl rand -hex 32`), `ADMIN_EMAIL`, `ADMIN_PASSWORD`,
`SEC_USER_AGENT`, and the `LINE_*` values (see LINE_SETUP.md). Secrets live only in `.env` (mode 600, gitignored).

## 3. TLS certificate (Let's Encrypt)

```bash
LETSENCRYPT_EMAIL=you@example.com scripts/init-letsencrypt.sh
```

The `certbot` service renews certificates every 12 h (webroot); Nginx reads them from the shared volume.
Reload Nginx after a renewal: `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec nginx nginx -s reload`
(add it to cron weekly).

## 4. Deploy

```bash
scripts/deploy.sh
```

The script validates `.env`, pulls the latest code, backs up the database if it is running, builds images, starts
the stack (migrations run automatically), and waits for `https://<domain>/health`.

Then load data once:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m app.cli load
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m app.cli fundamentals
```

## Production configuration summary

| Concern | How |
|---|---|
| Reverse proxy / HTTPS | Nginx (`nginx/conf.d/production.conf.template`), TLS 1.2/1.3, HSTS, HTTP→HTTPS redirect, ACME webroot |
| Restart policies | `restart: always` on all services |
| Health checks | container healthchecks (API `/health`, frontend, Nginx, Postgres, Redis); `deploy.sh` waits for `/health` |
| Persistent data | `pgdata`, `redisdata` volumes; logs in `./logs/{backend,worker,nginx}`; Docker json logs rotated (20 MB × 5) |
| Secrets | `.env` (600), never committed; `COOKIE_SECURE=true` set by the prod override |
| Rate limiting | Nginx (10 r/s API, 10 r/min login) + API per-IP limiter |

## Backups

```bash
scripts/backup.sh /opt/stock-monitor/backups
```

Creates a verified `pg_dump -Fc` archive with a SHA-256 checksum and deletes archives older than
`RETENTION_DAYS` (default 14). Schedule it, e.g. `crontab -e`:

```
30 2 * * * cd /opt/stock-monitor && scripts/backup.sh >> logs/backup.log 2>&1
```

Copy backups off the server (object storage / another host) — a backup on the same disk is not disaster recovery.

## Restore

```bash
scripts/restore.sh backups/stock-20261106T023000Z.dump
```

Verifies the checksum, asks for confirmation, stops the app containers, recreates the database, restores the dump
and restarts the app (migrations run on start). Test restores periodically on a staging host.

## Updates and rollback

`scripts/deploy.sh` performs updates. To roll back, `git checkout <previous tag>` and run `scripts/deploy.sh`; if a
migration must be reverted, restore the pre-deploy backup that `deploy.sh` created.
