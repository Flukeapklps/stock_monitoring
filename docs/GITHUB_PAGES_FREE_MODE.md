# Free mode: GitHub Actions + GitHub Pages + daily LINE plan

> คู่มือการใช้งานภาษาไทย (อ่านข้อความ LINE, หน้าเว็บ, สัญญาณ, การเพิ่มหุ้น): [USER_GUIDE_TH.md](USER_GUIDE_TH.md)

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
3. Variable `SEC_USER_AGENT` = `StockMonitor/1.0 your-email@example.com` (Settings → Secrets and variables → Actions → **Variables** tab). SEC rejects requests without contact info (HTTP 403); without it fundamentals are skipped and scores use technicals only.
4. Edit `snapshot.config.json` (symbols, names, ETFs under `asset_types`, `fundamentals: false` to skip SEC).
5. *Actions → Daily snapshot → Run workflow* for a first run (tick *force_line* to resend a session).

## Troubleshooting

- `Get Pages site failed … Not Found`: Pages is not enabled. Settings → Pages → Build and deployment →
  Source: **GitHub Actions**. Until then the workflow still builds the snapshot and sends LINE, and only skips
  the website (a warning is shown).
- `Branch "…" is not allowed to deploy to github-pages`: Settings → Environments → github-pages → Deployment
  branches: allow your default branch (or the branch you run from).
- Scheduled runs only start from the **default branch**.

## Cost and limits

- Free on public repositories; one run is ~2–3 minutes (private repos use the free Actions minutes quota).
- Uses ≤ ~30 LINE messages/month, within the free LINE Official Account message quota.
- Read-only dashboard; no login, no web editing (change symbols in the config file), no LINE bot commands.
- Scheduled Actions can start late (a backup run covers this). GitHub disables schedules in public repos after
  60 days without repository activity; the `Keep schedules alive` workflow re-enables them via the API on the
  1st and 20th of each month (no commits). If it is ever disabled anyway, GitHub emails you: re-enable it in the
  Actions tab.
- Yahoo Finance is unofficial and best-effort; a missing final bar is retried by the backup run.

Upgrade path: the full platform (`docker compose`, see PRODUCTION_DEPLOYMENT.md) adds login, watchlists,
portfolios, alerts per event and the interactive LINE bot.
