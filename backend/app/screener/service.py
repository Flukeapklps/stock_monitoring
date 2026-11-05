"""Long-term stock screener over the tracked universe (no hardcoded results)."""

from __future__ import annotations

import csv
import io
import operator
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import fundamentals as fund
from app.analysis.service import relative_performance
from app.models import Stock
from app.services.views import stock_rows

OPS = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le, "eq": operator.eq, "between": None, "is_true": None, "is_false": None}

FIELDS: dict[str, str] = {
    "price_above_sma200": "Price above SMA 200",
    "sma50_above_sma200": "SMA 50 above SMA 200",
    "rsi_14": "RSI (14)",
    "revenue_growth_yoy": "Revenue growth YoY (%)",
    "free_cash_flow": "Free cash flow (USD)",
    "fcf_margin": "FCF margin (%)",
    "rel_6m": "6M return vs benchmark (pts)",
    "rel_1y": "1Y return vs benchmark (pts)",
    "dist_52w_high_pct": "Distance from 52w high (%)",
    "score": "Investment score",
    "return_1y": "1Y return (%)",
    "operating_margin": "Operating margin (%)",
    "debt_to_equity": "Debt-to-equity",
    "weekly_above_sma40": "Weekly close above SMA 40",
    "asset_type": "Asset type",
}


class Filter(BaseModel):
    field: str
    op: str
    value: Any = None
    value2: Any = None


class ScreenRequest(BaseModel):
    filters: list[Filter] = []
    benchmark: str = "SPY"
    sort_by: str = "score"
    descending: bool = True
    limit: int = 200
    asset_types: list[str] | None = None


PRESETS: dict[str, dict] = {
    "long_term_leaders": {
        "label": "Long-term leaders",
        "description": "Uptrend, healthy momentum, profitable growth, outperforming SPY, near highs.",
        "filters": [
            {"field": "price_above_sma200", "op": "is_true"},
            {"field": "sma50_above_sma200", "op": "is_true"},
            {"field": "rsi_14", "op": "between", "value": 40, "value2": 60},
            {"field": "revenue_growth_yoy", "op": "gt", "value": 0},
            {"field": "free_cash_flow", "op": "gt", "value": 0},
            {"field": "rel_6m", "op": "gt", "value": 0},
            {"field": "dist_52w_high_pct", "op": "gte", "value": -15},
            {"field": "score", "op": "gt", "value": 70},
        ],
    },
    "accumulation_candidates": {
        "label": "Accumulation candidates",
        "description": "Long-term uptrend with RSI cooling into the 40-50 zone.",
        "filters": [
            {"field": "price_above_sma200", "op": "is_true"},
            {"field": "sma50_above_sma200", "op": "is_true"},
            {"field": "rsi_14", "op": "between", "value": 40, "value2": 50},
        ],
    },
    "quality_growth": {
        "label": "Quality growth",
        "description": "Positive revenue growth, positive FCF, operating margin > 15%.",
        "filters": [
            {"field": "revenue_growth_yoy", "op": "gt", "value": 0},
            {"field": "free_cash_flow", "op": "gt", "value": 0},
            {"field": "operating_margin", "op": "gt", "value": 15},
        ],
    },
    "trend_breakdown": {
        "label": "Trend breakdown (review)",
        "description": "Holdings to review: below SMA 200 and weekly close below SMA 40.",
        "filters": [
            {"field": "price_above_sma200", "op": "is_false"},
            {"field": "weekly_above_sma40", "op": "is_false"},
        ],
    },
}


def _apply(f: Filter, v: Any) -> bool:
    if f.op == "is_true":
        return v is True
    if f.op == "is_false":
        return v is False
    if v is None:
        return False  # missing data never passes a numeric filter
    if f.op == "between":
        return float(f.value) <= float(v) <= float(f.value2)
    if f.op == "eq":
        return str(v).upper() == str(f.value).upper()
    fn = OPS.get(f.op)
    if fn is None:
        raise ValueError(f"unknown operator {f.op}")
    return fn(float(v), float(f.value))


def run_screen(db: Session, req: ScreenRequest) -> dict:
    for f in req.filters:
        if f.field not in FIELDS:
            raise ValueError(f"unknown field {f.field}")
        if f.op not in OPS:
            raise ValueError(f"unknown operator {f.op}")
    q = select(Stock).where(Stock.is_tracked.is_(True), Stock.asset_type != "INDEX")
    if req.asset_types:
        q = q.where(Stock.asset_type.in_([a.upper() for a in req.asset_types]))
    stocks = list(db.scalars(q))
    rows = stock_rows(db, stocks)
    by_symbol = {s.symbol: s for s in stocks}
    needs_rel = any(f.field.startswith("rel_") for f in req.filters) or req.sort_by.startswith("rel_")
    results = []
    for r in rows:
        if r["price"] is None:
            continue
        st = by_symbol[r["symbol"]]
        r = dict(r)
        r["price_above_sma200"] = None if r["sma_200"] is None else r["price"] > r["sma_200"]
        r["sma50_above_sma200"] = None if r["sma_50"] is None or r["sma_200"] is None else r["sma_50"] > r["sma_200"]
        # fundamentals are always attached (shown in results / CSV)
        snap = fund.latest_snapshot(db, st.id).values
        for k in ("revenue_growth_yoy", "free_cash_flow", "fcf_margin", "operating_margin", "debt_to_equity"):
            r[k] = snap.get(k, {}).get("value")
        if needs_rel and r["trade_date"]:
            rel = relative_performance(db, st.id, date.fromisoformat(r["trade_date"]), req.benchmark)
            r["rel_6m"], r["rel_1y"] = rel.get("rel_6m"), rel.get("rel_1y")
        passed = []
        ok = True
        for f in req.filters:
            res = _apply(f, r.get(f.field))
            passed.append({"field": f.field, "label": FIELDS[f.field], "passed": res, "value": r.get(f.field)})
            ok &= res
        if ok:
            r["filter_results"] = passed
            r.pop("freshness", None)
            results.append(r)
    key = req.sort_by
    results.sort(key=lambda x: (x.get(key) is None, -(x.get(key) or 0) if req.descending else (x.get(key) or 0)))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return {"universe_size": len(stocks), "count": len(results), "results": results[: req.limit], "benchmark": req.benchmark}


CSV_COLUMNS = ["rank", "symbol", "name", "price", "trade_date", "change_pct", "return_1y", "rsi_14", "sma_50", "sma_200", "trend", "score", "score_classification", "revenue_growth_yoy", "free_cash_flow", "rel_6m", "dist_52w_high_pct", "latest_signal", "data_status", "provider_label"]


def to_csv(results: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in results:
        w.writerow(r)
    return buf.getvalue()
