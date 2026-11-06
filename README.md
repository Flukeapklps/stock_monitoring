# US Stock Long-Term Intelligence & Monitoring Platform

Long-term research and monitoring for US stocks and ETFs (NASDAQ, NYSE, NYSE American, US-listed ETFs):
end-of-day market data, technical indicators, rule-based long-term signals, a 0–100 investment score,
SEC fundamentals, a screener, backtesting, manual portfolios, and LINE Official Account alerts.

> **Research tool only.** It never executes trades. Signals and scores describe current technical and
> fundamental characteristics; they are not forecasts or investment advice. Market data is end-of-day
> and is never labelled as real-time.

## What it does

| Area | Highlights |
|---|---|
| Market data | Provider abstraction (Yahoo Finance → Alpha Vantage fallback, SEC EDGAR, optional FRED). Incremental updates, NYSE-calendar aware, per-record provider + `data_status` (END_OF_DAY / HISTORICAL / STALE …). |
| Data quality | Duplicates, invalid OHLC, non-trading-day rows, missing sessions, abnormal moves, zero volume, provider outages, staleness. Stale data never produces signals. |
| Indicators | SMA 20/50/100/200, EMA 20/50/200, ADX, Supertrend, RSI, MACD, ROC, Stoch RSI, Bollinger, ATR, HV, OBV, A/D, volume SMAs, 52-week range, returns (1M–5Y), drawdown, volatility. Verified against TA-Lib to ~1e-14. |
| Signals | Long-term trend, accumulation, overbought (monitor-only), risk; weekly confirmation; every signal stores indicator values, reasons, provider and confirmation status. |
| Score | Trend 30 / Momentum 20 / Fundamental quality 25 / Financial health 15 / Relative performance 10 (user-configurable). Missing data is excluded and reported as coverage — never treated as 0 or positive. |
| Fundamentals | SEC XBRL company facts: FY + TTM + balance sheet; reported vs calculated metrics; filings (10-K/10-Q/8-K). |
| Research | Screener with presets + CSV export; backtester (next-open fills, costs, slippage, dividend-adjusted, SPY/QQQ comparison). |
| Portfolio | Multiple portfolios, manual holdings or transactions (average cost), realized/unrealized P/L, dividends, allocation, time-weighted performance vs SPY. |
| LINE | Messaging API (push/reply/multicast/quota), signed webhook, bot commands, Flex alert cards, reports, preferences, cooldown, dedupe, quiet hours, retry keys, delivery history. |
| Dashboard | Next.js dark terminal: market overview, watchlists, stock detail charts, signal center, screener, backtest, portfolio, LINE settings, notification history, data health. |

## Free mode (GitHub Pages + daily LINE plan)

No server needed: a scheduled GitHub Action publishes a static dashboard to GitHub Pages and sends one LINE
message per trading day with the plan for the next session. See [GITHUB_PAGES_FREE_MODE.md](docs/GITHUB_PAGES_FREE_MODE.md).

## Quick start (local)

```bash
cp .env.example .env        # set ADMIN_PASSWORD and a random JWT_SECRET (openssl rand -hex 32)
```

```bash
docker compose up -d --build
```

```bash
docker compose exec backend python -m app.cli load          # ~10 years of daily history for the default universe
```

```bash
docker compose exec backend python -m app.cli fundamentals  # SEC fundamentals + filings
```

Open http://localhost:3000 and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. API docs: http://localhost:8000/api/docs.
`scripts/local-up.sh` does all of the above.

## Repository layout

```
backend/            FastAPI app, pipeline, analysis, LINE module, Alembic migrations, pytest suite
  app/providers/    MarketDataProvider + Yahoo / Alpha Vantage / SEC EDGAR / FRED
  app/pipeline/     ingestion, validation, resampling, repository (bulk upserts)
  app/indicators/   indicator math + per-timeframe computation
  app/analysis/     rules, scoring, fundamentals, analysis service
  app/integrations/line/  client, webhook, signature, flex templates, validator, commands, notifications, reports
  app/scheduler/    APScheduler worker (NYSE-calendar aware)
frontend/           Next.js 15 + TypeScript + Tailwind + shadcn-style UI, Vitest + Playwright
nginx/              local and production (TLS) reverse proxy configs
scripts/            deploy, backup, restore, Let's Encrypt, local bootstrap
docs/               architecture, schema, API, setup and operations guides
```

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, adjustment methodology, design decisions
- [DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md)
- [API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md)
- [MARKET_DATA_SETUP.md](docs/MARKET_DATA_SETUP.md)
- [LINE_SETUP.md](docs/LINE_SETUP.md) — including the manual end-to-end test with a real Official Account
- [LOCAL_DEPLOYMENT.md](docs/LOCAL_DEPLOYMENT.md)
- [PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md)
- [BACKTESTING_GUIDE.md](docs/BACKTESTING_GUIDE.md)
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [GITHUB_PAGES_FREE_MODE.md](docs/GITHUB_PAGES_FREE_MODE.md)

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && TEST_DATABASE_URL=postgresql+psycopg://stock:stock@localhost:5432/stock_test pytest
```

```bash
cd frontend && npm ci && npm test && npm run typecheck
```

```bash
cd frontend && E2E_BASE_URL=http://localhost:3000 E2E_PASSWORD=<admin password> npx playwright test
```

Backend: 92 tests (indicators vs TA-Lib reference values, validation, calendar, ingestion/upserts, rules,
scoring, fundamentals, providers, backtest bias controls, API, LINE with mocked API). Frontend: Vitest component
tests and a Playwright suite run on desktop and mobile viewports.
