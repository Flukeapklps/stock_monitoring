"""Exchange-calendar aware session helpers (NYSE calendar, America/New_York).

NASDAQ, NYSE, NYSE American and NYSE Arca share the NYSE holiday schedule, so the
XNYS calendar is used for all US listings. Weekends and holidays are never
treated as missing data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

NY = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _calendar():
    return mcal.get_calendar("NYSE")


@lru_cache(maxsize=32)
def _schedule(start: date, end: date) -> pd.DataFrame:
    return _calendar().schedule(start_date=start.isoformat(), end_date=end.isoformat())


def _year_window(d: date) -> tuple[date, date]:
    # cache by calendar-year-ish windows to keep lru hits high
    return date(d.year - 1, 1, 1), date(d.year + 1, 12, 31)


def schedule_between(start: date, end: date) -> pd.DataFrame:
    """Session schedule (market_open/market_close in UTC) indexed by session date."""
    if start > end:
        return _schedule(date(start.year, 1, 1), date(start.year, 12, 31)).iloc[0:0]
    frames = []
    y = start.year
    while y <= end.year:
        s, e = date(y, 1, 1), date(y, 12, 31)
        frames.append(_schedule(s, e))
        y += 1
    sched = pd.concat(frames) if frames else pd.DataFrame()
    sched = sched[~sched.index.duplicated()]
    idx = sched.index.date
    return sched[(idx >= start) & (idx <= end)]


def trading_days(start: date, end: date) -> list[date]:
    return [ts.date() for ts in schedule_between(start, end).index]


def is_trading_day(d: date) -> bool:
    return len(schedule_between(d, d)) == 1


def session_close_utc(d: date) -> datetime:
    """UTC close time for a session (handles early closes). Falls back to 16:00 ET."""
    sched = schedule_between(d, d)
    if len(sched):
        return sched.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY).astimezone(UTC)


def session_closes(start: date, end: date) -> dict[date, datetime]:
    """Map of session date -> UTC close for all sessions in [start, end]."""
    sched = schedule_between(start, end)
    return {ts.date(): row.to_pydatetime().astimezone(UTC) for ts, row in sched["market_close"].items()}


def latest_completed_session(now: datetime | None = None, settle_minutes: int = 0) -> date:
    """Most recent session whose close (plus settle time) is <= now."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    start = (now - timedelta(days=15)).date()
    sched = schedule_between(start, now.date())
    for ts, row in reversed(list(sched.iterrows())):
        close = row["market_close"].to_pydatetime().astimezone(UTC) + timedelta(minutes=settle_minutes)
        if close <= now:
            return ts.date()
    # extremely long closure: search further back
    return latest_completed_session(now - timedelta(days=15), settle_minutes)


def previous_session(d: date) -> date:
    days = trading_days(d - timedelta(days=15), d - timedelta(days=1))
    return days[-1]


def next_session(d: date) -> date:
    days = trading_days(d + timedelta(days=1), d + timedelta(days=20))
    return days[0]


def is_last_session_of_week(d: date) -> bool:
    """True if `d` is a trading day and the next session falls in a later ISO week."""
    if not is_trading_day(d):
        return False
    n = next_session(d)
    return (n - timedelta(days=n.weekday())) != (d - timedelta(days=d.weekday()))


def is_last_session_of_month(d: date) -> bool:
    if not is_trading_day(d):
        return False
    n = next_session(d)
    return (n.year, n.month) != (d.year, d.month)


def market_status(now: datetime | None = None) -> dict:
    """Return OPEN / CLOSED / PRE_MARKET / AFTER_HOURS with session times."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    today_ny = now.astimezone(NY).date()
    sched = schedule_between(today_ny, today_ny)
    latest = latest_completed_session(now)
    if not len(sched):
        return {"status": "CLOSED", "reason": "Weekend or market holiday", "latest_completed_session": latest.isoformat()}
    open_ = sched.iloc[0]["market_open"].to_pydatetime().astimezone(UTC)
    close = sched.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    if now < open_:
        status = "PRE_MARKET"
    elif now <= close:
        status = "OPEN"
    else:
        status = "AFTER_HOURS"
    return {
        "status": status,
        "session_open": open_.isoformat(),
        "session_close": close.isoformat(),
        "early_close": close.astimezone(NY).hour < 16,
        "latest_completed_session": latest.isoformat(),
    }
