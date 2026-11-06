# Architecture

## System diagram

```
                         ┌──────────────────────── Internet ────────────────────────┐
                         │  Browser            LINE Platform (webhook + Messaging API)│
                         └────┬───────────────────────┬───────────────▲──────────────┘
                              │ HTTPS                 │ POST webhook   │ push/reply/quota
                        ┌─────▼───────────────────────▼──┐            │
                        │ Nginx (TLS, HSTS, edge limits) │            │
                        └─────┬───────────────┬──────────┘            │
                     /        │               │ /api/*                │
              ┌───────────────▼───┐   ┌───────▼─────────────────────────┴────┐
              │ Next.js frontend  │   │ FastAPI (uvicorn)                     │
              │ (dashboard)       │──▶│  auth · stocks · watchlists · signals │
              └───────────────────┘   │  portfolios · screener · backtest     │
                                      │  system · LINE routes (webhook)       │
                                      └───┬──────────────┬───────────────────┘
                                          │              │
                         ┌────────────────▼──┐   ┌───────▼────────┐
                         │ PostgreSQL        │   │ Redis           │
                         │ (all state)       │   │ cache · quotas  │
                         └────────▲──────────┘   │ locks · limits  │
                                  │              └───────▲────────┘
                         ┌────────┴──────────────────────┴────────┐
                         │ Worker (APScheduler, NYSE calendar)     │
                         │  daily update → validate → indicators   │
                         │  → signals/scores → alerts → LINE queue │
                         │  fundamentals · reports · dispatcher    │
                         └────────┬────────────────────────────────┘
                                  │ provider layer (priority + fallback + health)
             ┌────────────────────┼──────────────────┬────────────────┐
         Yahoo Finance       Alpha Vantage        SEC EDGAR          FRED
        (best-effort EOD)   (key, 25 req/day)    (no key, UA)     (optional key)
```

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Providers | `app/providers/` | `MarketDataProvider` interface; Yahoo, Alpha Vantage, SEC EDGAR, FRED. Each result carries its provider name. Registry handles priority, fallback and health (`provider_status`). |
| Pipeline | `app/pipeline/ingestion.py` | Universe → symbol validation → fetch (batched) → validation → normalization → upsert daily → rebuild weekly/monthly → indicators. Incremental with a 10-day overlap. |
| Quality | `app/pipeline/quality.py` | Raw validation (drops invalid rows, flags suspicious ones), freshness vs. the exchange calendar, adjustment-change detection. |
| Indicators | `app/indicators/` | Pure pandas functions (TA-Lib conventions) and per-timeframe computation. |
| Analysis | `app/analysis/` | Rule engine (`rules.py`), score (`scoring.py`), SEC fundamentals (`fundamentals.py`), orchestration and persistence (`service.py`). |
| Research | `app/screener/`, `app/backtest/`, `app/portfolio/` | Screener, backtester, portfolio accounting. |
| LINE | `app/integrations/line/` | Independent service layer; reads analysis output, never imported by indicator/signal code. |
| Scheduler | `app/scheduler/runner.py` | Cron jobs in America/New_York, re-checked against the NYSE calendar. |
| API | `app/api/routes/`, `app/integrations/line/routes.py` | FastAPI endpoints, JWT cookie sessions, admin authorization. |
| Frontend | `frontend/src/` | Next.js App Router; TanStack Query for data; Lightweight Charts (price), Recharts (RSI/MACD/equity). |

## Market data ingestion workflow

1. **Universe** = `DEFAULT_UNIVERSE` + benchmarks + market overview symbols + every watched/held symbol.
2. **Symbol validation**: format check, then a new symbol must be confirmed by a provider (metadata or recent bars).
   A provider outage is reported as "cannot validate now", never as "unknown symbol".
3. **Fetch window**: no history → `INITIAL_HISTORY_YEARS`; otherwise from the last stored bar minus 10 days.
   Symbols already at the latest completed session are skipped (no request).
4. **Validation** drops duplicates, bars after the latest completed session (in-progress day), weekend/holiday rows
   and invalid OHLC; flags zero volume, abnormal moves (> `ABNORMAL_MOVE_THRESHOLD`, splits excluded) and missing
   sessions. Results go to `data_quality_logs`.
5. **Adjustment check**: if the overlap shows changed adjusted closes or new dividends/splits, the full history is
   re-downloaded (adjusted history is re-based by the provider).
6. **Upsert** with `ON CONFLICT … DO UPDATE WHERE values IS DISTINCT FROM` — unchanged rows are not rewritten.
   The latest bar is `END_OF_DAY`; older bars `HISTORICAL`.
7. **Weekly/monthly** bars are aggregated from daily bars (ISO week / calendar month, `is_complete` flag).
8. **Indicators** are recomputed from full history; only rows on/after the first new bar are written.
9. **Analysis** (worker): freshness gate → rules → score → signals (`investment_signals`, `investment_scores`).
10. **Alerts**: new signals, price rules, filings, deterioration, portfolio checks → LINE queue → dispatcher.

Timestamps are stored in UTC (`timestamp` = session close in UTC, early closes handled); session logic uses
America/New_York; the UI renders in the user's timezone.

## Adjustment methodology

- Yahoo `Close` is split-adjusted, not dividend-adjusted; `Adj Close` is split + dividend adjusted.
- **Price-level indicators** (MAs, RSI, MACD, Bollinger, ATR, ADX, Supertrend, 52-week range, support/resistance) use
  split-adjusted OHLC so levels match what investors see on charts.
- **Return metrics** (period returns, drawdown, volatility, relative strength, portfolio benchmark, backtests) use
  `adj_close` (total return). If a provider has no adjusted close (Alpha Vantage free `TIME_SERIES_DAILY`), `close` is
  used and an `NO_ADJUSTED_CLOSE` quality note is logged.
- **Look-ahead**: every indicator is causal (verified by a test that appending future bars does not change past
  values); signals use the latest *complete* weekly bar; backtests fill at the next session's open.

## Signal and score design

Rules (`app/analysis/rules.py`, configurable via `RuleConfig`):

- **Long-term trend BULLISH**: price > SMA200 and SMA50 > SMA200 and ≥3 of 5 bullish conditions
  (also SMA200 rising, weekly close > weekly SMA40, ADX ≥ 20 with +DI > −DI). **BEARISH** mirrors this.
- **Accumulation**: bullish trend + RSI in 40–50 + price within −1.5%…+3% of SMA50 or SMA200, plus at least one of
  MACD improving / volume declining / price stabilizing.
- **Overbought** (flag only, never a sell): RSI > 70 with another extension condition, or two extension conditions.
- **Risk / TREND_WEAKENING**: below SMA200 with weekly break or falling SMA200, or ≥3 risk conditions
  (death cross, large drawdown, negative MACD, fundamental deterioration…).

Primary signal = `ACCUMULATION_WATCH` if triggered, else the score classification
(80+ STRONG_LONG_TERM_TREND, 65 BULLISH, 50 NEUTRAL, 35 WEAKENING, else BEARISH). Confirmation is `CONFIRMED` when
the weekly trend agrees, `UNCONFIRMED` when it disagrees, `PENDING` without weekly data.

Score normalization: each sub-metric maps linearly between documented "worst" and "best" bounds (clipped to 0–100);
RSI uses a long-term profile (50–65 best). Missing sub-metrics are excluded; `coverage` = data-backed share of total
weight; coverage < 60% is flagged.

## LINE notification workflow

```
analysis result ─▶ alerts.py (new signal? user filters: min score, RSI zone, followed symbols)
                 ─▶ notification_service.enqueue
                      preferences & follow status ─ dedupe key ─ cooldown ─ 24h cap ─ quiet hours
                      ─▶ notification_history (event) + line_notification_history (QUEUED, retry key)
worker every 30 s ─▶ dispatch_pending (SELECT … FOR UPDATE SKIP LOCKED)
                      quota check ─▶ POST /v2/bot/message/push (X-Line-Retry-Key)
                      2xx/409 ─▶ ACCEPTED · 5xx/429/network ─▶ QUEUED + backoff (30s, 60s, 120s…)
                      timeout on last attempt ─▶ UNKNOWN · 4xx / monthly quota ─▶ FAILED
webhook ─▶ verify X-Line-Signature on raw body ─▶ store (dedupe webhookEventId) ─▶ 200
          ─▶ background: follow/unfollow/message/postback/join/leave ─▶ reply (single use)
```

## Security

bcrypt password hashing; JWT session in an HttpOnly SameSite=Lax cookie (Secure in production); admin role for
sending/administrative endpoints; Pydantic validation; SQLAlchemy parameterized queries; per-IP rate limiting
(Redis) with stricter login limits plus Nginx edge limits; CORS allow-list; security headers; webhook HMAC-SHA256
verification with constant-time comparison; tokens masked in API responses and redacted from logs; PostgreSQL and
Redis are never published in production.

## Design decisions

- **Indicators implemented in pandas** instead of depending on TA-Lib's C library or `pandas-ta`: no native build
  step, numpy 2/pandas 3 compatible, and verified against TA-Lib output stored in `tests/fixtures`.
- **Indicator storage as JSONB per bar** (`indicator_values`) rather than one row per indicator: one upsert per bar,
  and adding an indicator needs no migration.
- **DB-backed notification queue** instead of Celery: durable, inspectable history, `SKIP LOCKED` for concurrent
  workers. Celery can be introduced if task volume requires distributed workers.
- **LINE users map to platform users**: on follow a "line" shadow user is created so `/watch` works immediately;
  `/link CODE` attaches the LINE account to a web account.
