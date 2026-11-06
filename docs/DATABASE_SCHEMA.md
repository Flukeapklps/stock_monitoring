# Database Schema

PostgreSQL 16, managed with Alembic (`backend/alembic/versions`). All timestamps are `timestamptz` (UTC).
Migrations run automatically when the API container starts (`RUN_MIGRATIONS=true`); manually:
`docker compose exec backend alembic upgrade head`.

## Core

| Table | Key columns | Constraints / indexes |
|---|---|---|
| `users` | email, password_hash (bcrypt), role (`admin`/`user`/`line`), timezone, score_weights (JSONB), line_link_code | unique email, unique line_link_code |
| `stock_exchanges` | code (NASDAQ, NYSE, NYSEAMERICAN, ARCA, BATS, INDEX), mic, calendar_code | unique code |
| `stocks` | symbol, name, exchange_id → stock_exchanges, asset_type (EQUITY/ETF/INDEX), sector, industry, cik, shares_outstanding, is_tracked, metadata_source | unique symbol, index cik |
| `watchlists` | user_id → users, name, is_default | unique (user_id, name) |
| `watchlist_items` | watchlist_id, stock_id, notes, added_at | unique (watchlist_id, stock_id) |

## Market data

`market_data_daily`, `market_data_weekly`, `market_data_monthly` share:
`stock_id, trade_date, timestamp (UTC session close), open, high, low, close, adj_close (NUMERIC 18,6), volume,
provider, retrieved_at, data_status`.

- Daily adds `dividend`, `split_ratio`; weekly/monthly add `is_complete`.
- Unique `(stock_id, trade_date)` (`uq_market_data_*_stock_date`) — the upsert target.
- `data_status` ∈ LIVE, DELAYED, END_OF_DAY, HISTORICAL, STALE, UNAVAILABLE (this platform stores END_OF_DAY/HISTORICAL).

| Table | Purpose | Unique key |
|---|---|---|
| `indicator_values` | JSONB snapshot of all indicators per bar & timeframe | (stock_id, timeframe, trade_date) |
| `investment_signals` | signal_type, is_primary, score, confidence, confirmation_status, indicator_snapshot, reasons, provider, data_status | (stock_id, timeframe, signal_date, signal_type) |
| `investment_scores` | total_score, classification, coverage, components, weights, explanation, missing | (stock_id, score_date) |
| `fundamental_data` | metric, value, unit, period_type (FY/TTM/INSTANT), period_start/end, fiscal_year/period, form, filed_date, accession_number, is_calculated, source_tag, provider | (stock_id, metric, period_end, period_type) |
| `sec_filings` | cik, accession_number, form, filing_date, report_date, primary_document, url, notified | accession_number |
| `provider_status` | is_healthy, last_success/failure, last_error, consecutive_failures, requests_today, daily_quota | provider |
| `data_quality_logs` | check_type, severity, trade_date, message, details, provider | index (stock_id, created_at) |
| `system_logs` | level, component, event, message, details | index created_at |

## Portfolio & alerts

| Table | Purpose |
|---|---|
| `portfolios` | user_id, name (unique per user), base_currency, max_drawdown_alert_pct, max_position_pct |
| `portfolio_transactions` | BUY / SELL / DIVIDEND with trade_date, quantity, price, fees |
| `portfolio_holdings` | quantity, average_cost, purchase_date, realized_pl — unique (portfolio_id, stock_id); rebuilt from transactions when they exist |
| `alert_rules` | web-user rules (PRICE_ABOVE, PRICE_BELOW, ACCUMULATION_ZONE, RISK_THRESHOLD) with conditions JSONB, cooldown |
| `notification_history` | channel-agnostic alert events; unique (recipient_key, dedupe_key) prevents duplicate alerts |

## LINE

| Table | Purpose | Notes |
|---|---|---|
| `line_users` | line_user_id, display_name, is_following, user_id → users, timezone, followed/unfollowed_at | unique line_user_id |
| `line_notification_preferences` | user_id → line_users, notification_type, enabled, settings_json | unique (user_id, notification_type); type `GENERAL` holds min score, RSI zone, cooldown, quiet hours, timezone, extra symbols |
| `line_notification_history` | delivery queue + log: message_payload, status (QUEUED/SENDING/ACCEPTED/FAILED/UNKNOWN/SKIPPED), attempt_count, retry_key (X-Line-Retry-Key), line_request_id, http_status, api_response, error_message, next_attempt_at, created_at, sent_at | unique retry_key; index (status, next_attempt_at) |
| `line_webhook_events` | webhook_event_id, event_type, line_user_id, payload, processed, is_redelivery, error_message | unique webhook_event_id (duplicate events ignored) |
| `line_alert_rules` | LINE-user price rules: stock_symbol, rule_type, conditions_json, cooldown_minutes, last_triggered_at | index user_id |

## Write efficiency

- Bulk multi-row `INSERT … ON CONFLICT DO UPDATE … WHERE (columns) IS DISTINCT FROM excluded` — idempotent,
  unchanged rows are not rewritten (verified by a test).
- Indicators are rewritten only from the first changed bar onward.
- Signals use upsert with `RETURNING (xmax = 0)` so re-analysis refreshes records but only true inserts raise alerts.
