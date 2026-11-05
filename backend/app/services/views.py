"""Read models shared by the API, screener, LINE bot and reports."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.models import IndicatorValue, InvestmentScore, InvestmentSignal, MarketDataDaily, Stock
from app.pipeline.quality import freshness
from app.providers.registry import PROVIDER_LABELS


def _latest_per_stock(db: Session, model, date_col, stock_ids: list[int], extra_where=()):
    if not stock_ids:
        return {}
    q = (
        select(model)
        .where(model.stock_id.in_(stock_ids), *extra_where)
        .order_by(model.stock_id, date_col.desc())
        .ext(distinct_on(model.stock_id))
    )
    return {r.stock_id: r for r in db.scalars(q)}


def _prev_bars(db: Session, stock_ids: list[int]) -> dict[int, list[MarketDataDaily]]:
    """Latest two daily bars per stock (for daily change)."""
    if not stock_ids:
        return {}
    from sqlalchemy import func

    rn = func.row_number().over(partition_by=MarketDataDaily.stock_id, order_by=MarketDataDaily.trade_date.desc()).label("rn")
    sub = select(MarketDataDaily.id, rn).where(MarketDataDaily.stock_id.in_(stock_ids)).subquery()
    rows = db.scalars(select(MarketDataDaily).join(sub, sub.c.id == MarketDataDaily.id).where(sub.c.rn <= 2).order_by(MarketDataDaily.stock_id, MarketDataDaily.trade_date.desc()))
    out: dict[int, list[MarketDataDaily]] = {}
    for r in rows:
        out.setdefault(r.stock_id, []).append(r)
    return out


def stock_rows(db: Session, stocks: list[Stock]) -> list[dict]:
    ids = [s.id for s in stocks]
    bars = _prev_bars(db, ids)
    inds = _latest_per_stock(db, IndicatorValue, IndicatorValue.trade_date, ids, (IndicatorValue.timeframe == "DAILY",))
    weekly = _latest_per_stock(db, IndicatorValue, IndicatorValue.trade_date, ids, (IndicatorValue.timeframe == "WEEKLY",))
    scores = _latest_per_stock(db, InvestmentScore, InvestmentScore.score_date, ids)
    sigs = _latest_per_stock(db, InvestmentSignal, InvestmentSignal.signal_date, ids, (InvestmentSignal.is_primary.is_(True),))
    out = []
    for s in stocks:
        b = bars.get(s.id, [])
        last = b[0] if b else None
        prev = b[1] if len(b) > 1 else None
        iv = inds.get(s.id).values if s.id in inds else {}
        wv = weekly.get(s.id).values if s.id in weekly else {}
        price = float(last.close) if last and last.close is not None else None
        prev_close = float(prev.close) if prev and prev.close is not None else None
        sc = scores.get(s.id)
        sig = sigs.get(s.id)
        trend = None
        if iv.get("close") is not None and iv.get("sma_200") is not None and iv.get("sma_50") is not None:
            if iv["close"] > iv["sma_200"] and iv["sma_50"] > iv["sma_200"]:
                trend = "BULLISH"
            elif iv["close"] < iv["sma_200"] and iv["sma_50"] < iv["sma_200"]:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
        fr = freshness(last.trade_date if last else None)
        out.append(
            {
                "symbol": s.symbol,
                "name": s.name,
                "asset_type": s.asset_type,
                "exchange": s.exchange.code if s.exchange else None,
                "sector": s.sector,
                "price": price,
                "previous_close": prev_close,
                "change": None if price is None or prev_close is None else round(price - prev_close, 4),
                "change_pct": None if price is None or not prev_close else round((price / prev_close - 1) * 100, 4),
                "trade_date": last.trade_date.isoformat() if last else None,
                "return_1y": iv.get("return_1y"),
                "return_3m": iv.get("return_3m"),
                "return_6m": iv.get("return_6m"),
                "rsi_14": iv.get("rsi_14"),
                "sma_50": iv.get("sma_50"),
                "sma_200": iv.get("sma_200"),
                "dist_sma_200_pct": iv.get("dist_sma_200_pct"),
                "dist_52w_high_pct": iv.get("dist_52w_high_pct"),
                "macd_hist": iv.get("macd_hist"),
                "weekly_above_sma40": None if wv.get("close") is None or wv.get("sma_40") is None else wv["close"] > wv["sma_40"],
                "trend": trend,
                "score": sc.total_score if sc else None,
                "score_classification": sc.classification if sc else None,
                "score_coverage": sc.coverage if sc else None,
                "score_date": sc.score_date.isoformat() if sc else None,
                "latest_signal": sig.signal_type if sig else None,
                "latest_signal_date": sig.signal_date.isoformat() if sig else None,
                "provider": last.provider if last else None,
                "provider_label": PROVIDER_LABELS.get(last.provider, last.provider) if last else None,
                "data_status": (last.data_status if fr["status"] == "FRESH" else ("STALE" if fr["status"] == "STALE" else "UNAVAILABLE")) if last else "UNAVAILABLE",
                "freshness": fr,
            }
        )
    return out


def signal_dict(sig: InvestmentSignal, symbol: str, name: str | None = None) -> dict:
    return {
        "id": sig.id,
        "symbol": symbol,
        "name": name,
        "signal_date": sig.signal_date.isoformat(),
        "timeframe": sig.timeframe,
        "signal_type": sig.signal_type,
        "is_primary": sig.is_primary,
        "score": sig.score,
        "confidence": sig.confidence,
        "confirmation_status": sig.confirmation_status,
        "indicators": sig.indicator_snapshot,
        "reasons": sig.reasons,
        "provider": sig.provider,
        "provider_label": PROVIDER_LABELS.get(sig.provider, sig.provider),
        "data_status": sig.data_status,
        "created_at": sig.created_at.isoformat(),
    }


def score_dict(sc: InvestmentScore | None) -> dict | None:
    if not sc:
        return None
    return {
        "score_date": sc.score_date.isoformat(),
        "total": sc.total_score,
        "classification": sc.classification,
        "coverage": sc.coverage,
        "low_coverage": sc.coverage < 0.6,
        "components": sc.components,
        "weights": sc.weights,
        "explanation": sc.explanation,
        "missing": sc.missing,
        "disclaimer": "Score reflects current technical and fundamental characteristics; it is not a forecast or a guarantee of future returns.",
    }


def latest_close(db: Session, stock_id: int, on_or_before: date | None = None) -> MarketDataDaily | None:
    q = select(MarketDataDaily).where(MarketDataDaily.stock_id == stock_id)
    if on_or_before:
        q = q.where(MarketDataDaily.trade_date <= on_or_before)
    return db.scalar(q.order_by(MarketDataDaily.trade_date.desc()).limit(1))
