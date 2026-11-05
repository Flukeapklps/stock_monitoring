"""Compute the full indicator set for a timeframe.

Adjustment methodology (see docs/ARCHITECTURE.md):
  * Price-level indicators (SMA/EMA, RSI, MACD, Bollinger, ATR, ADX, Supertrend,
    52-week range, support/resistance) use split-adjusted OHLC (`close`, `high`, `low`).
    Yahoo's `Close` is split-adjusted but not dividend-adjusted, which keeps
    indicator levels comparable with the prices investors see on charts.
  * Return-based metrics (period returns, drawdown, volatility, relative strength,
    backtests) use `adj_close` (split + dividend adjusted = total return). When a
    provider has no adjusted close, `close` is used and `returns_basis` records it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.indicators import core

TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252, "3y": 756, "5y": 1260}


def _returns_series(df: pd.DataFrame) -> tuple[pd.Series, str]:
    if "adj_close" in df and df["adj_close"].notna().sum() >= max(1, int(0.95 * df["close"].notna().sum())):
        return df["adj_close"].astype(float), "adj_close"
    return df["close"].astype(float), "close"


def compute_daily(df: pd.DataFrame) -> pd.DataFrame:
    """df: index = session date, columns open/high/low/close/adj_close/volume (floats)."""
    if df.empty:
        return pd.DataFrame(index=df.index)
    c, h, l_, v = (df[k].astype(float) for k in ("close", "high", "low", "volume"))
    r, _basis = _returns_series(df)
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    for n in (20, 50, 100, 200):
        out[f"sma_{n}"] = core.sma(c, n)
    for n in (20, 50, 200):
        out[f"ema_{n}"] = core.ema(c, n)
    out = out.join(core.adx(h, l_, c, 14))
    out = out.join(core.supertrend(h, l_, c, 10, 3.0))
    out["rsi_14"] = core.rsi(c, 14)
    out = out.join(core.macd(c, 12, 26, 9))
    out["roc_12"] = core.roc(c, 12)
    out = out.join(core.stoch_rsi(c))
    out = out.join(core.bollinger(c, 20, 2.0))
    out["atr_14"] = core.atr(h, l_, c, 14)
    out["atr_pct"] = out["atr_14"] / c * 100
    out["hv_20"] = core.historical_volatility(r, 20)
    out["obv"] = core.obv(c, v)
    out["volume"] = v
    out["volume_sma_20"] = core.sma(v, 20)
    out["volume_sma_50"] = core.sma(v, 50)
    out["ad_line"] = core.ad_line(h, l_, c, v)

    # long-term metrics
    out["high_52w"] = h.rolling(252, min_periods=1).max()
    out["low_52w"] = l_.rolling(252, min_periods=1).min()
    out["dist_52w_high_pct"] = (c / out["high_52w"] - 1) * 100
    out["dist_52w_low_pct"] = (c / out["low_52w"] - 1) * 100
    out["dist_sma_50_pct"] = (c / out["sma_50"] - 1) * 100
    out["dist_sma_200_pct"] = (c / out["sma_200"] - 1) * 100
    out["sma_200_slope_20d_pct"] = (out["sma_200"] / out["sma_200"].shift(20) - 1) * 100
    out["sma_50_slope_10d_pct"] = (out["sma_50"] / out["sma_50"].shift(10) - 1) * 100
    for label, n in TRADING_DAYS.items():
        out[f"return_{label}"] = (r / r.shift(n) - 1) * 100
    out["return_1d"] = (r / r.shift(1) - 1) * 100
    logret = np.log(r / r.shift(1))
    out["volatility_1y"] = logret.rolling(252, min_periods=126).std(ddof=1) * math.sqrt(252) * 100
    out["drawdown_pct"] = core.drawdown(r)
    out["max_drawdown_1y"] = out["drawdown_pct"].rolling(252, min_periods=1).min()
    # Rolling-peak based drawdown over the trailing year
    roll_peak = r.rolling(252, min_periods=1).max()
    out["drawdown_from_1y_peak"] = (r / roll_peak - 1) * 100
    out["history_sessions"] = np.arange(1, len(df) + 1)
    # crossovers (state on this bar vs previous)
    out["golden_cross"] = (out["sma_50"] > out["sma_200"]) & (out["sma_50"].shift(1) <= out["sma_200"].shift(1))
    out["death_cross"] = (out["sma_50"] < out["sma_200"]) & (out["sma_50"].shift(1) >= out["sma_200"].shift(1))
    out["macd_bull_cross"] = (out["macd"] > out["macd_signal"]) & (out["macd"].shift(1) <= out["macd_signal"].shift(1))
    out["macd_bear_cross"] = (out["macd"] < out["macd_signal"]) & (out["macd"].shift(1) >= out["macd_signal"].shift(1))
    out["macd_hist_rising"] = out["macd_hist"] > out["macd_hist"].shift(1)
    return out


def compute_weekly(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(index=df.index)
    c, h, l_ = (df[k].astype(float) for k in ("close", "high", "low"))
    r, _ = _returns_series(df)
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["sma_10"] = core.sma(c, 10)
    out["sma_40"] = core.sma(c, 40)
    out["ema_20"] = core.ema(c, 20)
    out["sma_40_slope_4w_pct"] = (out["sma_40"] / out["sma_40"].shift(4) - 1) * 100
    out["rsi_14"] = core.rsi(c, 14)
    out = out.join(core.macd(c))
    out = out.join(core.adx(h, l_, c, 14))
    out["return_13w"] = (r / r.shift(13) - 1) * 100
    out["return_52w"] = (r / r.shift(52) - 1) * 100
    out["hv_52w"] = core.historical_volatility(r, 52, periods_per_year=52)
    if "is_complete" in df:
        out["is_complete"] = df["is_complete"].astype(bool)
    return out


def compute_monthly(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(index=df.index)
    c = df["close"].astype(float)
    r, _ = _returns_series(df)
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["sma_10"] = core.sma(c, 10)
    out["sma_12"] = core.sma(c, 12)
    out["rsi_14"] = core.rsi(c, 14)
    out = out.join(core.macd(c))
    out["return_1m"] = (r / r.shift(1) - 1) * 100
    out["return_12m"] = (r / r.shift(12) - 1) * 100
    if "is_complete" in df:
        out["is_complete"] = df["is_complete"].astype(bool)
    return out


def row_to_json(row: pd.Series) -> dict:
    """Convert an indicator row to JSON-safe dict (NaN -> None, numpy -> python)."""
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, (bool, np.bool_)):
            out[k] = bool(v)
        elif v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            out[k] = None
        elif isinstance(v, (np.floating, float)):
            fv = float(v)
            out[k] = None if math.isnan(fv) or math.isinf(fv) else round(fv, 6)
        elif isinstance(v, (np.integer, int)):
            out[k] = int(v)
        elif v is pd.NA or v is pd.NaT:
            out[k] = None
        else:
            out[k] = v
    return out


def support_resistance(df: pd.DataFrame, lookback: int = 252, window: int = 10, max_levels: int = 3) -> dict:
    """Pivot-based support/resistance from the trailing `lookback` sessions.

    A swing low/high is a bar whose low/high is the extreme of +/- `window` bars.
    Pivots within 1.5% are clustered; levels are ranked by touch count. Pivots
    need `window` future bars to be confirmed, so the most recent bars cannot
    create pivots (no look-ahead).
    """
    if df.empty:
        return {"support": [], "resistance": []}
    d = df.tail(lookback)
    lows, highs = d["low"].astype(float), d["high"].astype(float)
    last = float(d["close"].iloc[-1])
    span = 2 * window + 1
    piv_lo = lows[(lows == lows.rolling(span, center=True, min_periods=span).min())]
    piv_hi = highs[(highs == highs.rolling(span, center=True, min_periods=span).max())]

    def cluster(levels: list[float]) -> list[dict]:
        levels = sorted(levels)
        groups: list[list[float]] = []
        for lv in levels:
            if groups and abs(lv / np.mean(groups[-1]) - 1) <= 0.015:
                groups[-1].append(lv)
            else:
                groups.append([lv])
        return [{"level": round(float(np.mean(g)), 4), "touches": len(g)} for g in groups]

    sup = [x for x in cluster(list(piv_lo.values) + list(piv_hi.values)) if x["level"] < last]
    res = [x for x in cluster(list(piv_lo.values) + list(piv_hi.values)) if x["level"] > last]
    sup.sort(key=lambda x: (-x["level"]))
    res.sort(key=lambda x: (x["level"]))
    return {"support": sup[:max_levels], "resistance": res[:max_levels], "reference_close": last}
