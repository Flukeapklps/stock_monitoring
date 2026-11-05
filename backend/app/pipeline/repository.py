"""Database access helpers for market data: bulk upserts and frame loaders."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.market import calendar
from app.models import (
    DataQualityLog,
    IndicatorValue,
    MarketDataDaily,
    MarketDataMonthly,
    MarketDataWeekly,
    Stock,
    StockExchange,
    SystemLog,
)

BAR_MODELS = {"DAILY": MarketDataDaily, "WEEKLY": MarketDataWeekly, "MONTHLY": MarketDataMonthly}
_PRICE_COLS = ("open", "high", "low", "close", "adj_close")

EXCHANGES = [
    ("NASDAQ", "Nasdaq Stock Market", "XNAS"),
    ("NYSE", "New York Stock Exchange", "XNYS"),
    ("NYSEAMERICAN", "NYSE American", "XASE"),
    ("ARCA", "NYSE Arca", "ARCX"),
    ("BATS", "Cboe BZX", "BATS"),
    ("INDEX", "Market Index", None),
]


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def ensure_exchanges(db: Session) -> dict[str, int]:
    existing = {e.code: e.id for e in db.scalars(select(StockExchange))}
    for code, name, mic in EXCHANGES:
        if code not in existing:
            ex = StockExchange(code=code, name=name, mic=mic, calendar_code="XNYS")
            db.add(ex)
            db.flush()
            existing[code] = ex.id
    return existing


def get_stock(db: Session, symbol: str) -> Stock | None:
    return db.scalar(select(Stock).where(Stock.symbol == normalize_symbol(symbol)))


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


def upsert_bars(
    db: Session,
    timeframe: str,
    stock_id: int,
    frame: pd.DataFrame,
    provider: str,
    latest_session: date,
    retrieved_at: datetime | None = None,
) -> int:
    """Insert or update bars. Rows whose values are unchanged are not rewritten."""
    if frame is None or frame.empty:
        return 0
    model = BAR_MODELS[timeframe]
    retrieved_at = retrieved_at or datetime.now(UTC)
    rows = []
    closes = calendar.session_closes(min(frame.index), max(frame.index))
    for d, r in frame.iterrows():
        status = "END_OF_DAY" if d >= latest_session else "HISTORICAL"
        row = {
            "stock_id": stock_id,
            "trade_date": d,
            # weekly/monthly bars are stamped with the close of their last session
            "timestamp": closes.get(d) or calendar.session_close_utc(d),
            "open": _num(r.get("open")),
            "high": _num(r.get("high")),
            "low": _num(r.get("low")),
            "close": _num(r.get("close")),
            "adj_close": _num(r.get("adj_close")),
            "volume": int(r["volume"]) if _num(r.get("volume")) is not None else None,
            "provider": provider,
            "retrieved_at": retrieved_at,
            "data_status": status,
        }
        if timeframe == "DAILY":
            row["dividend"] = _num(r.get("dividend"))
            row["split_ratio"] = _num(r.get("split_ratio"))
        else:
            row["is_complete"] = bool(r.get("is_complete", True))
        rows.append(row)

    table = model.__table__
    count = 0
    for i in range(0, len(rows), 1000):
        chunk = rows[i : i + 1000]
        stmt = insert(table).values(chunk)
        changed_cols = [c for c in (*_PRICE_COLS, "volume") if c in table.c]
        if timeframe == "DAILY":
            changed_cols += ["dividend", "split_ratio"]
        else:
            changed_cols += ["is_complete"]
        set_ = {c: stmt.excluded[c] for c in (*changed_cols, "provider", "retrieved_at", "data_status", "timestamp")}
        where = or_(*[table.c[c].is_distinct_from(stmt.excluded[c]) for c in (*changed_cols, "provider", "data_status")])
        stmt = stmt.on_conflict_do_update(constraint=f"uq_{table.name}_stock_date", set_=set_, where=where)
        count += len(db.execute(stmt.returning(table.c.id)).all())
    # demote the previous END_OF_DAY bars
    db.execute(
        update(table)
        .where(and_(table.c.stock_id == stock_id, table.c.trade_date < latest_session, table.c.data_status == "END_OF_DAY"))
        .values(data_status="HISTORICAL")
    )
    return count


def load_bars(db: Session, timeframe: str, stock_id: int, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    model = BAR_MODELS[timeframe]
    cols = [model.trade_date, model.open, model.high, model.low, model.close, model.adj_close, model.volume, model.provider, model.data_status]
    if timeframe != "DAILY":
        cols.append(model.is_complete)
    else:
        cols += [model.dividend, model.split_ratio]
    q = select(*cols).where(model.stock_id == stock_id)
    if start:
        q = q.where(model.trade_date >= start)
    if end:
        q = q.where(model.trade_date <= end)
    q = q.order_by(model.trade_date)
    rows = db.execute(q).all()
    if not rows:
        return pd.DataFrame(columns=[c.key for c in cols]).set_index("trade_date")
    df = pd.DataFrame([r._asdict() for r in rows]).set_index("trade_date")
    for c in ("open", "high", "low", "close", "adj_close", "volume", "dividend", "split_ratio"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def latest_bar_date(db: Session, stock_id: int, timeframe: str = "DAILY") -> date | None:
    model = BAR_MODELS[timeframe]
    return db.scalar(select(model.trade_date).where(model.stock_id == stock_id).order_by(model.trade_date.desc()).limit(1))


def upsert_indicators(db: Session, stock_id: int, timeframe: str, rows: Iterable[tuple[date, dict]]) -> int:
    payload = [{"stock_id": stock_id, "timeframe": timeframe, "trade_date": d, "values": v, "calculated_at": datetime.now(UTC)} for d, v in rows]
    if not payload:
        return 0
    table = IndicatorValue.__table__
    total = 0
    for i in range(0, len(payload), 500):
        stmt = insert(table).values(payload[i : i + 500])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_indicator_stock_tf_date",
            set_={"values": stmt.excluded["values"], "calculated_at": stmt.excluded["calculated_at"]},
            where=table.c["values"].is_distinct_from(stmt.excluded["values"]),
        )
        total += len(db.execute(stmt.returning(table.c.id)).all())
    return total


def latest_indicators(db: Session, stock_id: int, timeframe: str = "DAILY", on_or_before: date | None = None) -> IndicatorValue | None:
    q = select(IndicatorValue).where(IndicatorValue.stock_id == stock_id, IndicatorValue.timeframe == timeframe)
    if on_or_before:
        q = q.where(IndicatorValue.trade_date <= on_or_before)
    return db.scalar(q.order_by(IndicatorValue.trade_date.desc()).limit(1))


def log_quality(db: Session, stock_id: int | None, issues, provider: str | None) -> None:
    for i in issues:
        db.add(
            DataQualityLog(
                stock_id=stock_id,
                check_type=i.check_type,
                severity=i.severity,
                trade_date=i.trade_date,
                message=i.message,
                details=i.details,
                provider=provider,
            )
        )


def system_log(db: Session, level: str, component: str, event: str, message: str | None = None, details: dict | None = None) -> None:
    db.add(SystemLog(level=level, component=component, event=event, message=message, details=details))
