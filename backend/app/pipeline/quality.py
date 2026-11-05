"""Raw data validation and data freshness checks.

Validation runs before anything is stored. Rows that are structurally invalid are
dropped (and logged); suspicious rows are kept but flagged. Signal generation is
blocked for a symbol when its latest bar is invalid or the data is stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from app.market import calendar


@dataclass
class QualityIssue:
    check_type: str
    severity: str  # INFO | WARNING | ERROR
    message: str
    trade_date: date | None = None
    details: dict | None = None


@dataclass
class ValidationReport:
    frame: pd.DataFrame
    issues: list[QualityIssue] = field(default_factory=list)
    dropped_dates: list[date] = field(default_factory=list)
    flagged_dates: list[date] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)


def validate_ohlcv(
    df: pd.DataFrame,
    *,
    asset_type: str = "EQUITY",
    latest_session: date | None = None,
    abnormal_threshold: float = 0.35,
    previous_close: float | None = None,
    check_missing_sessions: bool = True,
) -> ValidationReport:
    """Validate a provider frame indexed by session date."""
    issues: list[QualityIssue] = []
    dropped: list[date] = []
    flagged: list[date] = []
    if df is None or df.empty:
        return ValidationReport(pd.DataFrame(columns=getattr(df, "columns", [])), [QualityIssue("EMPTY", "WARNING", "provider returned no rows")])

    df = df.copy()
    for col in ("open", "high", "low", "close", "adj_close", "volume", "dividend", "split_ratio"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    # 1. duplicates
    dup_mask = pd.Index(df.index).duplicated(keep="last")
    if dup_mask.any():
        dups = sorted(set(pd.Index(df.index)[dup_mask]))
        issues.append(QualityIssue("DUPLICATE", "WARNING", f"{len(dups)} duplicate session rows removed", details={"dates": [str(d) for d in dups[:20]]}))
        df = df[~dup_mask]
    df = df.sort_index()

    # 2. incomplete / future sessions (never store an in-progress bar as EOD)
    latest_session = latest_session or calendar.latest_completed_session()
    future = [d for d in df.index if d > latest_session]
    if future:
        issues.append(QualityIssue("INCOMPLETE_SESSION", "INFO", f"dropped {len(future)} bar(s) after latest completed session {latest_session}", details={"dates": [str(d) for d in future]}))
        df = df.drop(index=future)
        dropped += future

    # 3. non-trading days (weekend/holiday rows from provider)
    if len(df):
        valid_days = set(calendar.trading_days(min(df.index), max(df.index)))
        bad_days = [d for d in df.index if d not in valid_days]
        if bad_days:
            issues.append(QualityIssue("NON_TRADING_DAY", "WARNING", f"dropped {len(bad_days)} row(s) on non-trading days", details={"dates": [str(d) for d in bad_days[:20]]}))
            df = df.drop(index=bad_days)
            dropped += bad_days

    # 4. invalid OHLC
    if len(df):
        o, h, l_, c = df["open"], df["high"], df["low"], df["close"]
        invalid = (
            c.isna()
            | (c <= 0)
            | (h.notna() & l_.notna() & (h < l_))
            | (h.notna() & (h < pd.concat([o, c], axis=1).max(axis=1) * (1 - 1e-6)))
            | (l_.notna() & (l_ > pd.concat([o, c], axis=1).min(axis=1) * (1 + 1e-6)))
            | (o.notna() & (o <= 0))
            | (df["volume"].notna() & (df["volume"] < 0))
        )
        if invalid.any():
            bad = list(df.index[invalid])
            for d in bad[:50]:
                r = df.loc[d]
                issues.append(QualityIssue("INVALID_OHLC", "ERROR", "invalid OHLC values; row dropped", trade_date=d, details={k: (None if pd.isna(r[k]) else float(r[k])) for k in ("open", "high", "low", "close", "volume")}))
            df = df[~invalid]
            dropped += bad

    # 5. zero / missing volume (indices legitimately have no volume)
    if len(df) and asset_type != "INDEX":
        zero = df["volume"].isna() | (df["volume"] == 0)
        if zero.any():
            zd = list(df.index[zero])
            issues.append(QualityIssue("ZERO_VOLUME", "WARNING", f"{len(zd)} session(s) with zero or missing volume", trade_date=zd[-1], details={"dates": [str(d) for d in zd[-20:]]}))
            flagged += zd

    # 6. abnormal price change (excluding split days)
    if len(df):
        closes = df["close"]
        prev = closes.shift(1)
        if previous_close is not None and len(prev):
            prev.iloc[0] = previous_close
        chg = closes / prev - 1
        split = df["split_ratio"].fillna(0) != 0 if "split_ratio" in df else pd.Series(False, index=df.index)
        abn = chg.abs() > abnormal_threshold
        abn &= ~split
        if abn.any():
            for d in df.index[abn]:
                issues.append(QualityIssue("ABNORMAL_MOVE", "WARNING", f"close changed {chg.loc[d] * 100:.1f}% vs previous session", trade_date=d, details={"change_pct": round(float(chg.loc[d]) * 100, 2)}))
                flagged.append(d)

    # 7. missing sessions inside the returned range
    if check_missing_sessions and len(df) > 1:
        expected = calendar.trading_days(min(df.index), max(df.index))
        have = set(df.index)
        missing = [d for d in expected if d not in have]
        if missing:
            sev = "WARNING" if len(missing) <= 5 else "ERROR"
            issues.append(QualityIssue("MISSING_SESSIONS", sev, f"{len(missing)} trading session(s) missing", details={"dates": [str(d) for d in missing[:50]]}))

    return ValidationReport(df, issues, dropped, flagged)


def freshness(latest_bar: date | None, now: datetime | None = None, settle_minutes: int = 60) -> dict:
    """Classify data freshness against the exchange calendar.

    `settle_minutes` gives providers time after the close to publish the final bar.
    Data is FRESH when it contains the latest completed (settled) session; STALE
    when it is behind; UNAVAILABLE when there is no data.
    """
    now = now or datetime.now(UTC)
    expected = calendar.latest_completed_session(now, settle_minutes=settle_minutes)
    if latest_bar is None:
        return {"status": "UNAVAILABLE", "expected_session": expected.isoformat(), "latest_bar": None, "sessions_behind": None}
    if latest_bar >= expected:
        return {"status": "FRESH", "expected_session": expected.isoformat(), "latest_bar": latest_bar.isoformat(), "sessions_behind": 0}
    behind = len(calendar.trading_days(latest_bar, expected)) - 1
    return {"status": "STALE", "expected_session": expected.isoformat(), "latest_bar": latest_bar.isoformat(), "sessions_behind": int(behind)}


def detect_adjustment_change(stored: pd.Series, incoming: pd.Series, tolerance: float = 1e-4) -> bool:
    """True if overlapping adjusted closes differ (new dividend/split re-based history)."""
    common = stored.index.intersection(incoming.index)
    if len(common) == 0:
        return False
    a = stored.loc[common].astype(float).to_numpy()
    b = incoming.loc[common].astype(float).to_numpy()
    mask = ~(np.isnan(a) | np.isnan(b))
    if not mask.any():
        return False
    return bool((np.abs(a[mask] / b[mask] - 1) > tolerance).any())
