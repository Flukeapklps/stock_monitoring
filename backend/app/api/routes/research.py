"""Screener, backtesting, rule configuration."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analysis.rules import RuleConfig
from app.analysis.scoring import COMPONENT_LABELS, DEFAULT_WEIGHTS
from app.backtest.engine import StrategyConfig, run_backtest
from app.core.config import get_settings
from app.core.security import get_current_user
from app.db.session import get_db
from app.models import User
from app.pipeline import repository as repo
from app.screener.service import FIELDS, PRESETS, ScreenRequest, run_screen, to_csv

router = APIRouter(prefix="/api/v1", tags=["research"])


@router.get("/screener/presets")
def presets(_u: User = Depends(get_current_user)):
    return {"presets": PRESETS, "fields": FIELDS, "operators": ["gt", "gte", "lt", "lte", "eq", "between", "is_true", "is_false"]}


@router.post("/screener/run")
def screen(req: ScreenRequest, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    try:
        return run_screen(db, req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/screener/export", response_class=PlainTextResponse)
def export(req: ScreenRequest, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    try:
        res = run_screen(db, req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return PlainTextResponse(to_csv(res["results"]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="screener-{date.today().isoformat()}.csv"'})


class BacktestIn(BaseModel):
    symbol: str
    start: date | None = None
    end: date | None = None
    strategy: StrategyConfig = StrategyConfig()
    benchmarks: list[str] | None = None


@router.post("/backtest/run")
def backtest(body: BacktestIn, db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    st = repo.get_stock(db, body.symbol)
    if not st:
        raise HTTPException(404, f"{body.symbol} is not tracked")
    daily = repo.load_bars(db, "DAILY", st.id)
    if len(daily) < 260:
        raise HTTPException(422, "at least ~1 year of history is required")
    bmarks = {}
    for b in body.benchmarks or ["SPY", "QQQ"]:
        bs = repo.get_stock(db, b)
        if bs and bs.id != st.id:
            bmarks[bs.symbol] = repo.load_bars(db, "DAILY", bs.id)
    try:
        res = run_backtest(st.symbol, daily, body.strategy, body.start, body.end, bmarks)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    providers = sorted(set(daily["provider"].dropna()))
    return {
        "symbol": res.symbol,
        "start": res.start.isoformat(),
        "end": res.end.isoformat(),
        "metrics": res.metrics,
        "benchmarks": res.benchmarks,
        "trades": [{**t.__dict__, "entry_date": t.entry_date.isoformat(), "exit_date": t.exit_date.isoformat() if t.exit_date else None} for t in res.trades],
        "equity_curve": res.equity_curve,
        "assumptions": res.assumptions,
        "strategy": body.strategy.model_dump(),
        "data_providers": providers,
    }


@router.get("/config/analysis")
def analysis_config(_u: User = Depends(get_current_user)):
    s = get_settings()
    return {"rules": RuleConfig().model_dump(), "score_weights": DEFAULT_WEIGHTS, "score_components": COMPONENT_LABELS, "benchmarks": s.benchmark_list, "classification_bands": {"STRONG_LONG_TERM_TREND": "80-100", "BULLISH": "65-79", "NEUTRAL": "50-64", "WEAKENING": "35-49", "BEARISH": "0-34"}}
