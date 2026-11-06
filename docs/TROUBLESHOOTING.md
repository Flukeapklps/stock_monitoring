# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `could not validate X: market data providers unavailable` | Yahoo (and fallbacks) unreachable. Check outbound HTTPS to `query1/query2.finance.yahoo.com`, `fc.yahoo.com`; see Data Health → Providers → last error. The symbol is not marked invalid. |
| `unknown symbol: X` | No provider knows the ticker. Use Yahoo notation for share classes (`BRK-B`; `BRK.B` is converted). |
| All symbols `STALE` | The daily job has not run since the last session. Data Health → `daily_update` → Run now, or `python -m app.cli daily`. Check `docker compose logs worker`. |
| Symbol `STALE` but others fresh | Provider returned no bar for the last session (halt, delisting, provider gap). Data Health → quality log; signals stay paused until fresh. |
| `ABNORMAL_MOVE` warnings | A > 35% daily move (config `ABNORMAL_MOVE_THRESHOLD`). Verify against another source; volatility indices (^VIX) trigger this legitimately. |
| `NON_TRADING_DAY` / `INCOMPLETE_SESSION` | Provider returned holiday rows or an in-progress bar; they were dropped by design. |
| Alpha Vantage `RateLimited` / `NotSupported` | Free tier: 25 requests/day; full history and adjusted daily are premium. Leave Yahoo first in `PROVIDER_PRIORITY`. |
| SEC requests failing (403) | `SEC_USER_AGENT` must contain an app name and contact email. |
| Fundamentals "unavailable" | ETFs/indices have no SEC company facts; some companies don't report a tag (missing metrics are shown, never zero). |
| Score marked `*` / low coverage | < 60% of score weight had data (often missing fundamentals). Run `python -m app.cli fundamentals`. |
| LINE webhook Verify fails | URL must be public HTTPS and reach `/api/v1/line/webhook`; `LINE_CHANNEL_SECRET` must match. Invalid signature → 401 in backend logs. |
| LINE test: `LINE API 401` | Wrong/expired `LINE_CHANNEL_ACCESS_TOKEN`; reissue it in the Developers Console. |
| LINE: `Recipient is not registered` | The user must follow the Official Account first; then use their ID from LINE Integration → Recipient. |
| LINE status `FAILED … to … invalid` | The recipient ID is wrong or the user blocked the account. |
| LINE `monthly message quota exhausted` | Check `/line/quota`; upgrade the plan or wait for the monthly reset. Messages are not retried. |
| No alerts although signals appear | Check preferences (type enabled, min score, RSI zone), that the symbol is on the user's watchlist/portfolio, cooldown, 24h cap, quiet hours (deferred), and Notification History statuses. |
| Buttons missing in LINE cards | Buttons are rendered only for HTTPS `PUBLIC_BASE_URL`. |
| `429 Rate limit exceeded` | Per-IP limit (`RATE_LIMIT_PER_MINUTE`, login stricter). Behind Nginx ensure `X-Real-IP` is set (it is in the provided config). |
| `bootstrap failed (have migrations been applied?)` | Run `docker compose exec backend alembic upgrade head`. |
| `JWT_SECRET must be set in production` | Set a ≥ 32-char random secret in `.env`. |
| Frontend calls fail with 502/504 | The frontend proxies `/api` to `BACKEND_URL` (build arg); in production Nginx routes `/api` directly. Check `docker compose ps` health. |
| Disk filling up | Docker logs rotate in production; prune old images (`docker image prune`), check `backups/` retention. |
