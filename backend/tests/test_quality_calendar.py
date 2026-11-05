from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from app.market import calendar
from app.pipeline.quality import detect_adjustment_change, freshness, validate_ohlcv
from app.pipeline.resample import to_monthly, to_weekly
from tests.conftest import synthetic_ohlcv


def test_calendar_holidays_and_early_close():
    assert not calendar.is_trading_day(date(2026, 11, 26))  # Thanksgiving
    assert calendar.is_trading_day(date(2026, 11, 27))
    assert calendar.session_close_utc(date(2026, 11, 27)).hour == 18  # 13:00 ET early close
    assert not calendar.is_trading_day(date(2026, 4, 3))  # Good Friday
    assert calendar.is_last_session_of_week(date(2026, 4, 2))


def test_latest_completed_session_weekend():
    # Sunday -> previous Friday
    assert calendar.latest_completed_session(datetime(2026, 11, 8, 15, tzinfo=UTC)) == date(2026, 11, 6)
    # Tuesday 15:00 ET (market still open) -> Monday
    assert calendar.latest_completed_session(datetime(2026, 11, 10, 20, tzinfo=UTC)) == date(2026, 11, 9)


def _frame(days):
    return synthetic_ohlcv(days, seed=1)


def test_validate_drops_invalid_ohlc_and_duplicates():
    days = calendar.trading_days(date(2026, 1, 5), date(2026, 3, 31))
    df = _frame(days)
    df.loc[days[10], "high"] = df.loc[days[10], "low"] * 0.5  # high < low
    df.loc[days[11], "close"] = -1
    dup = df.iloc[[5]]
    df = pd.concat([df, dup])
    rep = validate_ohlcv(df, latest_session=date(2026, 3, 31))
    types = {i.check_type for i in rep.issues}
    assert {"INVALID_OHLC", "DUPLICATE"} <= types
    assert days[10] in rep.dropped_dates and days[11] in rep.dropped_dates
    assert rep.frame.index.is_unique
    # dropped invalid rows create a missing-session warning
    assert "MISSING_SESSIONS" in types


def test_validate_flags_zero_volume_abnormal_move_and_weekend():
    days = calendar.trading_days(date(2026, 1, 5), date(2026, 2, 27))
    df = _frame(days)
    df.loc[days[3], "volume"] = 0
    df.loc[days[8]:, ["open", "high", "low", "close", "adj_close"]] *= 1.6
    weekend = pd.DataFrame(df.iloc[[0]].values, columns=df.columns, index=[date(2026, 1, 10)])
    df = pd.concat([df, weekend]).sort_index()
    rep = validate_ohlcv(df, latest_session=date(2026, 2, 27))
    types = {i.check_type for i in rep.issues}
    assert {"ZERO_VOLUME", "ABNORMAL_MOVE", "NON_TRADING_DAY"} <= types
    assert date(2026, 1, 10) not in rep.frame.index


def test_validate_index_allows_zero_volume_and_drops_incomplete_session():
    days = calendar.trading_days(date(2026, 1, 5), date(2026, 1, 30))
    df = _frame(days)
    df["volume"] = 0
    rep = validate_ohlcv(df, asset_type="INDEX", latest_session=date(2026, 1, 29))
    types = {i.check_type for i in rep.issues}
    assert "ZERO_VOLUME" not in types
    assert "INCOMPLETE_SESSION" in types
    assert max(rep.frame.index) == date(2026, 1, 29)


def test_freshness():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)  # Friday morning
    assert freshness(date(2026, 11, 5), now)["status"] == "FRESH"
    st = freshness(date(2026, 11, 3), now)
    assert st["status"] == "STALE" and st["sessions_behind"] == 2
    assert freshness(None, now)["status"] == "UNAVAILABLE"


def test_weekend_is_not_stale():
    sat = datetime(2026, 11, 7, 18, tzinfo=UTC)
    assert freshness(date(2026, 11, 6), sat)["status"] == "FRESH"


def test_adjustment_change_detection():
    a = pd.Series([1.0, 2.0, 3.0], index=[1, 2, 3])
    assert not detect_adjustment_change(a, a.copy())
    assert detect_adjustment_change(a, a * 0.99)


def test_weekly_monthly_resample():
    days = calendar.trading_days(date(2026, 3, 2), date(2026, 4, 8))
    df = _frame(days)
    w = to_weekly(df, latest_session=date(2026, 4, 8))
    # week of Mar 30 ends Thursday Apr 2 (Good Friday) and is complete
    assert date(2026, 4, 2) in w.index and bool(w.loc[date(2026, 4, 2), "is_complete"])
    assert not bool(w.iloc[-1]["is_complete"])  # current week in progress
    wk = df.loc[date(2026, 3, 2):date(2026, 3, 6)]
    row = w.loc[date(2026, 3, 6)]
    assert row["open"] == wk["open"].iloc[0] and row["close"] == wk["close"].iloc[-1]
    assert row["high"] == wk["high"].max() and row["volume"] == wk["volume"].sum()
    m = to_monthly(df, latest_session=date(2026, 4, 8))
    assert bool(m.loc[date(2026, 3, 31), "is_complete"]) and not bool(m.iloc[-1]["is_complete"])
    assert np.isclose(m.loc[date(2026, 3, 31), "low"], df.loc[date(2026, 3, 2):date(2026, 3, 31), "low"].min())
