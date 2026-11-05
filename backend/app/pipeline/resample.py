"""Build weekly / monthly OHLCV from daily bars.

Weekly bars group sessions by ISO week (Mon-Sun); monthly by calendar month. The
bar's `trade_date` is its last session. `is_complete` is False while the
week/month is still in progress (i.e. its last session is not the final session
of that period per the exchange calendar).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.market import calendar


def _aggregate(df: pd.DataFrame, keys: list) -> pd.DataFrame:
    work = df.copy()
    work["_key"] = keys
    work["_date"] = list(df.index)
    g = work.groupby("_key", sort=True)
    out = pd.DataFrame(
        {
            "trade_date": g["_date"].last(),
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "adj_close": g["adj_close"].last() if "adj_close" in work else None,
            "volume": g["volume"].sum(min_count=1),
            "sessions": g["close"].count(),
        }
    )
    return out.set_index("trade_date")


def to_weekly(daily: pd.DataFrame, latest_session: date) -> pd.DataFrame:
    if daily.empty:
        return daily
    keys = [d - timedelta(days=d.weekday()) for d in daily.index]
    out = _aggregate(daily, keys)
    out["is_complete"] = [
        calendar.is_last_session_of_week(d) or (d < latest_session and not _same_week(d, latest_session))
        for d in out.index
    ]
    return out


def to_monthly(daily: pd.DataFrame, latest_session: date) -> pd.DataFrame:
    if daily.empty:
        return daily
    keys = [(d.year, d.month) for d in daily.index]
    out = _aggregate(daily, keys)
    out["is_complete"] = [
        calendar.is_last_session_of_month(d) or ((d.year, d.month) != (latest_session.year, latest_session.month))
        for d in out.index
    ]
    return out


def _same_week(a: date, b: date) -> bool:
    return a - timedelta(days=a.weekday()) == b - timedelta(days=b.weekday())
