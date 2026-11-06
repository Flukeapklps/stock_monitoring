# Free mode: GitHub Actions + GitHub Pages + daily LINE plan

No server, no database, no cost. Once per trading day after the US close, a GitHub Action:

1. downloads ~3 years of end-of-day data (Yahoo Finance) for the symbols in `snapshot.config.json`,
2. adds SEC fundamentals, computes indicators, signals, scores (same engine as the full platform),
3. publishes a static dashboard (`site/`) to GitHub Pages,
4. sends **one** LINE message: the plan for the next session (consider accumulating / review / hold / watch,
   plus signal changes since yesterday).

Runs: 22:15 UTC Mon–Fri, with a backup run at 01:30 UTC for when Yahoo publishes the final bar late. The message
is sent once per session; holidays are skipped; symbols with stale or invalid data get no signal.

## Setup (once)

1. **Pages:** repository *Settings → Pages → Build and deployment → Source: GitHub Actions*.
   GitHub Pages on a private repository needs a paid plan; on a public repo the site (and your watchlist) is public.
2. **LINE** (Messaging API channel, see `LINE_SETUP.md` steps 1–2 — no webhook needed in this mode):
   *Settings → Secrets and variables → Actions → New repository secret*:
   - `LINE_CHANNEL_ACCESS_TOKEN` — long-lived channel access token
   - `LINE_USER_ID` — your user ID (Developers Console → Basic settings → *Your user ID*). Add the Official Account as a friend first.
3. Optional variable `SEC_USER_AGENT` = `StockMonitor/1.0 your-email@example.com` (SEC asks for contact info).
4. Edit `snapshot.config.json` (symbols, names, ETFs under `asset_types`, `fundamentals: false` to skip SEC).
5. *Actions → Daily snapshot → Run workflow* for a first run (tick *force_line* to resend a session).

## Cost and limits

- Free on public repositories; one run is ~2–3 minutes (private repos use the free Actions minutes quota).
- Uses ≤ ~30 LINE messages/month, within the free LINE Official Account message quota.
- Read-only dashboard; no login, no web editing (change symbols in the config file), no LINE bot commands.
- Scheduled Actions can start late; GitHub disables schedules after 60 days without repository activity
  (re-enable in the Actions tab).
- Yahoo Finance is unofficial and best-effort; a missing final bar is retried by the backup run.

Upgrade path: the full platform (`docker compose`, see PRODUCTION_DEPLOYMENT.md) adds login, watchlists,
portfolios, alerts per event and the interactive LINE bot.
