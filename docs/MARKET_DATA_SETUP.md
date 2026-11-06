# Market Data Setup

## Providers

| Provider | Key | Used for | Notes |
|---|---|---|---|
| Yahoo Finance (`yfinance`) | none | daily OHLCV (raw + adjusted), dividends, splits, metadata, forward P/E | **Unofficial, best-effort.** Not a guaranteed or real-time API. Retries, batching, incremental updates and an outage probe are built in. Respect Yahoo's terms. |
| Alpha Vantage | `ALPHA_VANTAGE_API_KEY` | fallback daily history, weekly/monthly, GLOBAL_QUOTE, OVERVIEW, EARNINGS, DIVIDENDS, SPLITS | Free tier documents **25 requests/day**; `outputsize=full` and adjusted daily are premium; US real-time / 15-min delayed quotes are not in the free tier. The client enforces `ALPHA_VANTAGE_DAILY_LIMIT`, spaces requests, caches responses and detects "Note/Information" (limit/premium) responses. Verify your plan's entitlements. |
| SEC EDGAR | none | CIK mapping, submissions (10-K/10-Q/8-K), XBRL company facts | Set `SEC_USER_AGENT` to `AppName/version contact@email` (SEC fair access). Requests are spaced to < 10/s and cached. |
| FRED | `FRED_API_KEY` (optional) | Fed funds, CPI, unemployment, 2Y/10Y yields | Used only when the key is set. |

Priority/fallback: `PROVIDER_PRIORITY=yahoo,alpha_vantage`. Each stored bar records its provider; mixing providers
in one history is logged as a `PROVIDER_MIX` quality warning, never done silently.

## Network access

The backend needs outbound HTTPS to: `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `fc.yahoo.com`,
`www.sec.gov`, `data.sec.gov`, and optionally `www.alphavantage.co`, `api.stlouisfed.org`, plus `api.line.me` for LINE.

## Loading data

```bash
docker compose exec backend python -m app.cli load                   # default universe + watched symbols
```

```bash
docker compose exec backend python -m app.cli load AAPL MSFT --years 10
```

```bash
docker compose exec backend python -m app.cli load NVDA --full       # force a full re-download
```

```bash
docker compose exec backend python -m app.cli fundamentals           # SEC facts + filings for tracked equities
```

Adding a symbol from the dashboard, `POST /stocks`, or the LINE `/watch` command validates it with a provider and
loads its history in the background.

## Update schedule (worker, America/New_York, Mon–Fri, holidays skipped)

| Time (default) | Job |
|---|---|
| `DAILY_JOB_TIME` 17:30 | prices → validation → indicators → signals/scores → alerts |
| `LINE_DAILY_REPORT_TIME` 17:45 | daily report |
| `LINE_WEEKLY_REPORT_TIME` 18:45 | weekly report, only on the last session of the week |
| `MONTHLY_JOB_TIME` 19:00 | monthly report, only on the last session of the month |
| `SEC_FILINGS_JOB_TIME` 20:00 | SEC fundamentals + new filing alerts |
| every 30 s | LINE notification dispatcher |

Weekly/monthly bars and indicators are rebuilt from daily data on every update, so no separate request is made.

## Freshness

Data is `FRESH` when the latest bar equals the latest completed NYSE session (with 60 min settle time after the
close), `STALE` when behind, `UNAVAILABLE` with no data. Weekends and holidays are never stale. Stale symbols get
no new signals and are listed on the Data Health page.
