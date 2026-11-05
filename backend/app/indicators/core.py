"""Technical indicator implementations (pure pandas / numpy).

Conventions follow TA-Lib so values can be cross-checked:
  * SMA: simple rolling mean, NaN until `n` observations.
  * EMA: seeded with the SMA of the first `n` values, then alpha = 2/(n+1).
  * RSI / ATR / ADX: Wilder smoothing (alpha = 1/n) seeded with simple averages.
  * Bollinger Bands: population standard deviation (ddof=0).
Every function only uses data at or before each row (no look-ahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _seeded_recursive(values: np.ndarray, n: int, alpha: float) -> np.ndarray:
    """Recursive smoothing seeded with the mean of the first `n` valid values."""
    out = np.full(len(values), np.nan)
    valid = np.where(~np.isnan(values))[0]
    if len(valid) < n:
        return out
    first = valid[0]
    # require contiguous valid block for the seed
    seed_end = first + n - 1
    if seed_end >= len(values) or np.isnan(values[first : seed_end + 1]).any():
        return out
    out[seed_end] = values[first : seed_end + 1].mean()
    prev = out[seed_end]
    for i in range(seed_end + 1, len(values)):
        v = values[i]
        if np.isnan(v):
            out[i] = prev
            continue
        prev = prev + alpha * (v - prev)
        out[i] = prev
    return out


def ema(s: pd.Series, n: int) -> pd.Series:
    vals = s.astype(float).to_numpy()
    return pd.Series(_seeded_recursive(vals, n, 2.0 / (n + 1)), index=s.index)


def wilder(s: pd.Series, n: int) -> pd.Series:
    vals = s.astype(float).to_numpy()
    return pd.Series(_seeded_recursive(vals, n, 1.0 / n), index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD with TA-Lib alignment: the fast EMA is seeded on the same bar as the
    slow EMA (SMA of the `fast` values ending at bar slow-1), and the MACD line is
    reported only once the signal line exists."""
    vals = close.astype(float).to_numpy()
    slow_ema = _seeded_recursive(vals, slow, 2.0 / (slow + 1))
    offset = slow - fast
    fast_part = _seeded_recursive(vals[offset:], fast, 2.0 / (fast + 1)) if len(vals) > offset else np.array([])
    fast_ema = np.concatenate([np.full(min(offset, len(vals)), np.nan), fast_part])
    line = pd.Series(fast_ema - slow_ema, index=close.index)
    sig = ema(line, signal)
    line = line.where(sig.notna())
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * std, mid - k * std
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"bb_middle": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pct_b": pct_b})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1, skipna=False)
    tr.iloc[0] = np.nan  # TA-Lib convention: TR undefined on first bar
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def _wilder_sum(s: pd.Series, n: int) -> pd.Series:
    """Wilder running sum used by DMI/ADX: S[n-1] = sum(x[1..n-1]); S[i] = S[i-1] - S[i-1]/n + x[i]."""
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return pd.Series(out, index=s.index)
    acc = np.nansum(x[1:n])
    out[n - 1] = acc
    for i in range(n, len(x)):
        acc = acc - acc / n + (0.0 if np.isnan(x[i]) else x[i])
        out[i] = acc
    out[n - 1] = np.nan  # first DI value is reported on bar n
    return pd.Series(out, index=s.index)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(high, low, close)
    # TA-Lib running sums: seeded with the sum of the first n-1 values (bars 1..n-1)
    str_ = _wilder_sum(tr, n)
    plus_di = 100 * _wilder_sum(plus_dm, n) / str_
    minus_di = 100 * _wilder_sum(minus_dm, n) / str_
    denom = plus_di + minus_di
    dx = (100 * (plus_di - minus_di).abs() / denom).where(denom != 0, 0.0)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def roc(close: pd.Series, n: int = 12) -> pd.Series:
    return (close / close.shift(n) - 1) * 100


def stoch_rsi(close: pd.Series, rsi_n: int = 14, stoch_n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    r = rsi(close, rsi_n)
    lo = r.rolling(stoch_n, min_periods=stoch_n).min()
    hi = r.rolling(stoch_n, min_periods=stoch_n).max()
    raw = ((r - lo) / (hi - lo) * 100).where(hi != lo, 50.0).where(r.notna() & lo.notna())
    k_line = raw.rolling(k, min_periods=k).mean()
    d_line = k_line.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"stoch_rsi_k": k_line, "stoch_rsi_d": d_line})


def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(high, low, close, n).to_numpy()
    h, l_, c = high.to_numpy(float), low.to_numpy(float), close.to_numpy(float)
    hl2 = (h + l_) / 2
    basic_up, basic_dn = hl2 + mult * a, hl2 - mult * a
    final_up = np.full(len(c), np.nan)
    final_dn = np.full(len(c), np.nan)
    trend = np.full(len(c), np.nan)
    line = np.full(len(c), np.nan)
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(final_up[i - 1]):
            final_up[i], final_dn[i], trend[i] = basic_up[i], basic_dn[i], 1
        else:
            final_up[i] = basic_up[i] if (basic_up[i] < final_up[i - 1] or c[i - 1] > final_up[i - 1]) else final_up[i - 1]
            final_dn[i] = basic_dn[i] if (basic_dn[i] > final_dn[i - 1] or c[i - 1] < final_dn[i - 1]) else final_dn[i - 1]
            if trend[i - 1] == 1:
                trend[i] = -1 if c[i] < final_dn[i] else 1
            else:
                trend[i] = 1 if c[i] > final_up[i] else -1
        line[i] = final_dn[i] if trend[i] == 1 else final_up[i]
    return pd.DataFrame({"supertrend": line, "supertrend_dir": trend}, index=close.index)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume; starts from the first bar's volume (TA-Lib convention)."""
    direction = np.sign(close.diff()).fillna(0)
    vol = volume.fillna(0)
    base = float(vol.iloc[0]) if len(vol) else 0.0
    return base + (direction * vol).cumsum()


def ad_line(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    rng = high - low
    mfm = (((close - low) - (high - close)) / rng).where(rng != 0, 0.0)
    return (mfm * volume.fillna(0)).cumsum()


def historical_volatility(close: pd.Series, n: int = 20, periods_per_year: int = 252) -> pd.Series:
    logret = np.log(close / close.shift(1))
    return logret.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(periods_per_year) * 100


def drawdown(series: pd.Series) -> pd.Series:
    peak = series.cummax()
    return (series / peak - 1) * 100


def max_drawdown(series: pd.Series) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    return float(drawdown(s).min())
