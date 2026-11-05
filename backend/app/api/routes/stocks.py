from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.analysis import fundamentals as fund
from app.analysis.rules import RuleConfig, evaluate
from app.analysis.scoring import compute_score
from app.analysis.service import relative_performance, relative_strength_series
from app.core.security import get_current_user, require_admin
from app.db.session import get_db
from app.indicators.compute import support_resistance
from app.models import FundamentalData, IndicatorValue, InvestmentScore, InvestmentSignal, SecFiling, Stock, User
from app.pipeline import repository as repo
from app.pipeline.ingestion import InvalidSymbol, ValidationUnavailable, ensure_stock, validate_symbol_format
from app.pipeline.quality import freshness
from app.providers.registry import PROVIDER_LABELS
from app.services import jobs
from app.services.views import score_dict, signal_dict, stock_rows

router = APIRouter(prefix="/api/v1/stocks", tags=["stocks"])
TIMEFRAMES = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}


def get_stock_or_404(db: Session, symbol: str) -> Stock:
    try:
        sym = validate_symbol_format(symbol)
    except InvalidSymbol as exc:
        raise HTTPException(422, str(exc)) from None
    st = repo.get_stock(db, sym)
    if not st:
        raise HTTPException(404, f"{sym} is not tracked")
    return st


@router.get("")
def list_stocks(q: str | None = None, limit: int = Query(100, le=1000), db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    query = select(Stock).where(Stock.is_tracked.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Stock.symbol.ilike(like), Stock.name.ilike(like)))
    stocks = list(db.scalars(query.order_by(Stock.symbol).limit(limit)))
    return stock_rows(db, stocks)


class AddStockIn(BaseModel):
    symbol: str


@router.post("")
def add_stock(body: AddStockIn, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    try:
        st = ensure_stock(db, body.symbol)
    except InvalidSymbol as exc:
        raise HTTPException(422, str(exc)) from None
    except ValidationUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    st.is_tracked = True
    db.commit()
    loading = repo.latest_bar_date(db, st.id) is None
    if loading:
        jobs.submit_symbol_load(st.symbol)
    return {"symbol": st.symbol, "name": st.name, "loading": loading}


@router.get("/{symbol}")
def stock_detail(symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    row = stock_rows(db, [st])[0]
    ind = repo.latest_indicators(db, st.id, "DAILY")
    wk = repo.latest_indicators(db, st.id, "WEEKLY")
    sc = db.scalar(select(InvestmentScore).where(InvestmentScore.stock_id == st.id).order_by(InvestmentScore.score_date.desc()).limit(1))
    daily = repo.load_bars(db, "DAILY", st.id, start=date.today() - timedelta(days=400))
    sr = support_resistance(daily) if not daily.empty else {"support": [], "resistance": []}
    snap = fund.latest_snapshot(db, st.id)
    rules = evaluate(ind.values, wk.values if wk else None, RuleConfig(), fund.detect_deterioration(db, st.id)).as_dict() if ind else None
    personal_score = None
    if user.score_weights and ind:
        rel = relative_performance(db, st.id, ind.trade_date)
        personal_score = compute_score(ind.values, wk.values if wk else None, snap.values, rel, user.score_weights).as_dict()
    return {
        "stock": {
            "symbol": st.symbol,
            "name": st.name,
            "asset_type": st.asset_type,
            "exchange": st.exchange.code if st.exchange else None,
            "sector": st.sector,
            "industry": st.industry,
            "currency": st.currency,
            "cik": st.cik,
            "metadata_source": PROVIDER_LABELS.get(st.metadata_source or "", st.metadata_source),
        },
        "quote": row,
        "indicators": ind.values if ind else None,
        "indicators_date": ind.trade_date.isoformat() if ind else None,
        "weekly_indicators": wk.values if wk else None,
        "support_resistance": sr,
        "score": score_dict(sc),
        "personal_score": personal_score,
        "rules": rules,
        "fundamentals": {"as_of": snap.as_of.isoformat() if snap.as_of else None, "values": snap.values, "missing": snap.missing, "summary": fund.fundamental_summary(snap), "valuation": fund.valuation(snap, row["price"])},
        "freshness": freshness(repo.latest_bar_date(db, st.id)),
    }


@router.get("/{symbol}/bars")
def bars(symbol: str, timeframe: str = "daily", start: date | None = None, end: date | None = None, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    tf = TIMEFRAMES.get(timeframe.lower())
    if not tf:
        raise HTTPException(422, "timeframe must be daily, weekly or monthly")
    if tf == "DAILY" and start is None:
        start = date.today() - timedelta(days=365 * 2)
    df = repo.load_bars(db, tf, st.id, start=start, end=end)
    out = []
    for d, r in df.iterrows():
        out.append(
            {
                "date": d.isoformat(),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "adj_close": None if r["adj_close"] != r["adj_close"] else r["adj_close"],
                "volume": None if r["volume"] != r["volume"] else int(r["volume"]),
                "provider": r["provider"],
                "data_status": r["data_status"],
                **({"is_complete": bool(r["is_complete"])} if "is_complete" in r else {}),
            }
        )
    providers = sorted({b["provider"] for b in out})
    return {"symbol": st.symbol, "timeframe": tf, "bars": out, "providers": [PROVIDER_LABELS.get(p, p) for p in providers]}


@router.get("/{symbol}/indicators")
def indicators(symbol: str, timeframe: str = "daily", start: date | None = None, fields: str | None = None, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    tf = TIMEFRAMES.get(timeframe.lower(), "DAILY")
    if tf == "DAILY" and start is None:
        start = date.today() - timedelta(days=365 * 2)
    q = select(IndicatorValue).where(IndicatorValue.stock_id == st.id, IndicatorValue.timeframe == tf)
    if start:
        q = q.where(IndicatorValue.trade_date >= start)
    rows = db.scalars(q.order_by(IndicatorValue.trade_date)).all()
    keep = set(fields.split(",")) if fields else None
    return {"symbol": st.symbol, "timeframe": tf, "rows": [{"date": r.trade_date.isoformat(), **({k: v for k, v in r.values.items() if k in keep} if keep else r.values)} for r in rows]}


@router.get("/{symbol}/signals")
def signals(symbol: str, limit: int = Query(100, le=1000), db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    rows = db.scalars(select(InvestmentSignal).where(InvestmentSignal.stock_id == st.id).order_by(InvestmentSignal.signal_date.desc()).limit(limit)).all()
    return [signal_dict(s, st.symbol, st.name) for s in rows]


@router.get("/{symbol}/score")
def score(symbol: str, history: bool = False, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    if history:
        rows = db.scalars(select(InvestmentScore).where(InvestmentScore.stock_id == st.id).order_by(InvestmentScore.score_date)).all()
        return [{"date": r.score_date.isoformat(), "total": r.total_score, "classification": r.classification, "coverage": r.coverage} for r in rows]
    sc = db.scalar(select(InvestmentScore).where(InvestmentScore.stock_id == st.id).order_by(InvestmentScore.score_date.desc()).limit(1))
    if not sc:
        raise HTTPException(404, "no score yet")
    return score_dict(sc)


@router.get("/{symbol}/fundamentals")
def fundamentals(symbol: str, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    rows = db.scalars(select(FundamentalData).where(FundamentalData.stock_id == st.id).order_by(FundamentalData.metric, FundamentalData.period_end)).all()
    hist: dict[str, list] = {}
    for r in rows:
        hist.setdefault(r.metric, []).append(
            {"period_end": r.period_end.isoformat(), "period_type": r.period_type, "value": r.value, "unit": r.unit, "fiscal_year": r.fiscal_year, "form": r.form, "filed_date": r.filed_date.isoformat() if r.filed_date else None, "is_calculated": r.is_calculated, "source": r.source_tag}
        )
    snap = fund.latest_snapshot(db, st.id)
    last = repo.latest_bar_date(db, st.id)
    price = None
    if last:
        from app.services.views import latest_close

        b = latest_close(db, st.id)
        price = float(b.close) if b and b.close is not None else None
    return {"symbol": st.symbol, "latest": snap.values, "missing": snap.missing, "history": hist, "summary": fund.fundamental_summary(snap), "valuation": fund.valuation(snap, price), "provider": "SEC EDGAR"}


@router.get("/{symbol}/filings")
def filings(symbol: str, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    rows = db.scalars(select(SecFiling).where(SecFiling.stock_id == st.id).order_by(SecFiling.filing_date.desc()).limit(50)).all()
    return [{"form": f.form, "filing_date": f.filing_date.isoformat(), "report_date": f.report_date.isoformat() if f.report_date else None, "accession_number": f.accession_number, "url": f.url} for f in rows]


@router.get("/{symbol}/relative")
def relative(symbol: str, benchmark: str = "SPY", start: date | None = None, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = get_stock_or_404(db, symbol)
    b = get_stock_or_404(db, benchmark)
    last = repo.latest_bar_date(db, st.id)
    if not last:
        raise HTTPException(404, "no data")
    return {"symbol": st.symbol, "benchmark": b.symbol, "summary": relative_performance(db, st.id, last, b.symbol), "rs_line": relative_strength_series(db, st.id, b.id, start or date.today() - timedelta(days=730))}


@router.post("/{symbol}/refresh")
def refresh(symbol: str, full: bool = False, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    from app.analysis.service import analyze_stock, default_benchmark
    from app.pipeline.ingestion import update_symbols

    st = get_stock_or_404(db, symbol)
    rep = update_symbols(db, [st.symbol], full_refresh=full)
    res = analyze_stock(db, st, benchmark=default_benchmark())
    db.commit()
    fres = fund.refresh_fundamentals(db, st) if st.asset_type == "EQUITY" else {"status": "SKIPPED"}
    db.commit()
    return {"ingestion": rep.summary(), "analysis_status": res.status, "primary_signal": res.primary_signal, "fundamentals": fres}
