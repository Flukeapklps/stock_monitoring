# Backtesting Guide

Dashboard → **Backtest**, or `POST /api/v1/backtest/run`.

## Default strategy

**BUY** (all enabled conditions true at the close of day *t*):
- Price above SMA 200
- SMA 50 above SMA 200
- RSI(14) between `rsi_min` (40) and `rsi_max` (55)
- MACD bullish crossover within the last `macd_cross_lookback` sessions (3)

**SELL / REVIEW** (first that applies at the close of day *t*):
- Risk threshold: close `stop_loss_pct` (15%) below the entry price
- Weekly close below weekly SMA 40 (last *completed* week only)
- Long-term trend reversal: close < SMA 200 and SMA 50 < SMA 200

Orders fill at the **open of day t+1**. One position at a time, fully invested, long only.

```json
{
  "symbol": "AAPL",
  "start": "2018-01-01",
  "end": null,
  "benchmarks": ["SPY", "QQQ"],
  "strategy": {
    "require_price_above_sma200": true, "require_sma50_above_sma200": true,
    "rsi_min": 40, "rsi_max": 55, "require_macd_bull_cross": true, "macd_cross_lookback": 3,
    "exit_weekly_below_sma40": true, "exit_trend_reversal": true, "stop_loss_pct": 15,
    "commission_bps": 5, "slippage_bps": 5, "initial_capital": 10000, "risk_free_rate_pct": 0
  }
}
```

## Metrics

Total return, CAGR, maximum drawdown, Sharpe and Sortino (daily, annualized √252, configurable risk-free rate),
annualized volatility, win rate, profit factor (gross profit / gross loss), number of trades, average holding
period, exposure, final equity. The same return metrics are reported for buy-and-hold of the symbol and of each
benchmark (SPY, QQQ) over the identical window, plus an equity curve for all of them.

## Bias controls and assumptions

| Issue | Handling |
|---|---|
| Look-ahead bias | Indicators are causal (tested); decisions at the close, fills at the next open; weekly rules use only completed weeks. |
| Splits & dividends | Simulation runs on adjusted OHLC (OHLC × adj_close/close) — dividends reinvested, splits neutral. |
| Costs | Commission + slippage in basis points applied adversely to every fill. |
| Indicator warm-up | The full stored history warms up indicators before `start`. |
| Survivorship bias | Only symbols you test are included; delisted companies are not. Testing today's winners overstates results. |
| Data quality | Uses stored, validated end-of-day bars; the providers used are returned in `data_providers`. |
| Open position | A position still open at the end is marked to market and labelled as such. |

Past performance does not guarantee future results. A backtest is a research aid, not a recommendation.
