# API Documentation

Interactive OpenAPI docs: `/api/docs` (JSON: `/api/openapi.json`). All endpoints are under `/api/v1` unless noted.
Authentication: `POST /api/v1/auth/login` sets an HttpOnly `sm_session` cookie and also returns a bearer token
(`Authorization: Bearer …`). 🔒 = signed-in user, 🛡 = admin.

## Auth
| Method | Path | |
|---|---|---|
| POST | `/auth/login` | `{email, password}` |
| POST | `/auth/logout` | clears the cookie |
| POST | `/auth/register` | only if `ALLOW_REGISTRATION=true` |
| GET/PUT 🔒 | `/auth/me` | profile; `timezone`, `score_weights` (custom score weights) |
| POST 🔒 | `/auth/password` | change password |

## Market & stocks 🔒
| Method | Path | |
|---|---|---|
| GET | `/market/overview` | indices/ETFs with price, change, data status, market session status |
| GET | `/market/macro` | FRED series (if `FRED_API_KEY`) |
| GET | `/stocks?q=` | tracked stocks with watchlist columns |
| POST | `/stocks` | `{symbol}` start tracking (validated with a provider; history loads in background) |
| GET | `/stocks/{symbol}` | quote, indicators (daily+weekly), rule evaluation, score (+ personal score), support/resistance, fundamentals summary & valuation, freshness |
| GET | `/stocks/{symbol}/bars?timeframe=daily\|weekly\|monthly&start&end` | OHLCV with provider + data_status |
| GET | `/stocks/{symbol}/indicators?timeframe&start&fields=sma_50,rsi_14` | indicator series |
| GET | `/stocks/{symbol}/signals` | signal history |
| GET | `/stocks/{symbol}/score?history=true` | latest score or score history |
| GET | `/stocks/{symbol}/fundamentals` | latest metrics, history by period, valuation |
| GET | `/stocks/{symbol}/filings` | SEC filings |
| GET | `/stocks/{symbol}/relative?benchmark=SPY` | relative returns and RS line |
| POST 🛡 | `/stocks/{symbol}/refresh?full=false` | re-ingest, analyze, refresh fundamentals |

## Watchlists, signals 🔒
| Method | Path | |
|---|---|---|
| GET/POST | `/watchlists` | list / create `{name, description, is_default}` |
| GET/PUT/DELETE | `/watchlists/{id}` | detail rows / update / delete |
| POST | `/watchlists/{id}/items` | `{symbol}` |
| DELETE | `/watchlists/{id}/items/{symbol}` | |
| GET | `/signals?signal_type=A,B&symbol&since&primary_only&confirmation&limit` | signal center |
| GET | `/signals/summary` | latest primary signal per stock grouped by type |

## Portfolios 🔒
| Method | Path | |
|---|---|---|
| GET/POST | `/portfolios` | list with totals / create `{name, base_currency, max_drawdown_alert_pct, max_position_pct}` |
| GET/PUT/DELETE | `/portfolios/{id}` | holdings with market value, P/L, allocation, dividends |
| GET | `/portfolios/{id}/performance?benchmark=SPY` | daily value, time-weighted index, drawdown, benchmark |
| POST | `/portfolios/{id}/holdings` | manual `{symbol, quantity, average_cost, purchase_date, notes}` |
| DELETE | `/portfolios/{id}/holdings/{holding_id}` | |
| GET/POST | `/portfolios/{id}/transactions` | `{symbol, tx_type: BUY\|SELL\|DIVIDEND, trade_date, quantity, price, fees}` |
| DELETE | `/portfolios/{id}/transactions/{tx_id}` | |

## Research 🔒
| Method | Path | |
|---|---|---|
| GET | `/screener/presets` | presets, fields, operators |
| POST | `/screener/run` | `{filters:[{field, op, value, value2}], benchmark, sort_by, descending, limit, asset_types}` |
| POST | `/screener/export` | same body → CSV |
| POST | `/backtest/run` | `{symbol, start, end, strategy:{…}, benchmarks:["SPY","QQQ"]}` — see BACKTESTING_GUIDE |
| GET | `/config/analysis` | rule defaults, score weights, classification bands |

## System
| Method | Path | |
|---|---|---|
| GET | `/health` (also `/api/v1/health`) | DB + cache check (no auth) |
| GET 🔒 | `/system/status` | market status, providers, freshness, stale symbols, last job runs |
| GET 🔒 | `/system/data-quality?severity&symbol` | quality log |
| GET 🛡 | `/system/logs` | system log |
| POST 🛡 | `/system/jobs/{job}/run?wait=false` | `daily_update`, `fundamentals_update`, `daily_report`, `weekly_report`, `monthly_report`, `dispatch_notifications` |

## LINE
| Method | Path | |
|---|---|---|
| POST | `/line/webhook` | public; requires valid `X-Line-Signature` (401 otherwise) |
| GET 🔒 | `/line/status` | connection, webhook, last message, error counts (token masked) |
| GET 🛡 | `/line/quota?refresh=true` | quota + consumption |
| GET 🔒 | `/line/history?status&symbol&notification_type&limit&offset` | delivery history (own recipients; admin: all) |
| GET 🛡 | `/line/users` | registered LINE users |
| GET/PUT 🔒 | `/line/preferences?line_user_id=` | per-type toggles + general settings |
| POST/DELETE 🔒 | `/line/alert-rules[/{id}]` | price rules |
| POST 🔒 | `/line/link-code` | code for `/link CODE` |
| POST 🛡 | `/line/test` | test message (also validated by LINE's validate endpoint) |
| POST 🛡 | `/line/connection-test` | bot info + webhook endpoint info |
| POST 🛡 | `/line/send` | `{line_user_ids?, text? , messages?}` validated messages to registered recipients only |
| POST 🛡 | `/line/alerts/test` | `{symbol, signal, line_user_id?}` sample alert card |
| POST 🛡 | `/line/reports/daily` · `/line/reports/weekly` · `/line/reports/monthly` | `{line_user_id?}` |
| GET 🔒 | `/line/preview/{symbol}?signal=` | Flex payload for dashboard preview |

Errors: `401` not signed in, `403` not admin, `404` unknown/untracked, `409` conflict, `422` validation,
`429` rate limited (`Retry-After: 60`), `503` providers unavailable for symbol validation.
