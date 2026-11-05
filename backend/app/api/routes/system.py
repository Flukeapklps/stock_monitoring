from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.cache import backend
from app.core.config import get_settings
from app.core.security import get_current_user, require_admin
from app.db.session import get_db
from app.market.calendar import latest_completed_session, market_status
from app.models import DataQualityLog, MarketDataDaily, Stock, SystemLog, User
from app.pipeline.quality import freshness
from app.providers.registry import provider_health
from app.services import jobs

router = APIRouter(tags=["system"])


@router.get("/health")
@router.get("/api/v1/health")
def health(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        backend().ping()
        checks["cache"] = "ok"
    except Exception:
        checks["cache"] = "error"
    ok = checks["database"] == "ok"
    if not ok:
        raise HTTPException(503, {"status": "unhealthy", "checks": checks})
    return {"status": "ok", "checks": checks}


@router.get("/api/v1/system/status")
def status(db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    latest_per_stock = (
        select(MarketDataDaily.stock_id, func.max(MarketDataDaily.trade_date).label("d")).group_by(MarketDataDaily.stock_id).subquery()
    )
    rows = db.execute(select(Stock.symbol, latest_per_stock.c.d).join(latest_per_stock, latest_per_stock.c.stock_id == Stock.id)).all()
    fresh = {"FRESH": 0, "STALE": 0, "UNAVAILABLE": 0}
    stale = []
    for sym, d in rows:
        f = freshness(d)
        fresh[f["status"]] += 1
        if f["status"] != "FRESH":
            stale.append({"symbol": sym, **f})
    tracked = db.scalar(select(func.count()).select_from(Stock).where(Stock.is_tracked.is_(True)))
    fresh["UNAVAILABLE"] += max(0, tracked - len(rows))
    last_update = db.scalar(select(func.max(MarketDataDaily.retrieved_at)))
    return {
        "market": market_status(),
        "latest_completed_session": latest_completed_session().isoformat(),
        "last_successful_update": last_update,
        "providers": provider_health(db),
        "freshness": fresh,
        "stale_symbols": stale[:50],
        "tracked_symbols": tracked,
        "jobs": jobs.last_runs(),
        "scheduler_enabled": get_settings().scheduler_enabled,
    }


@router.get("/api/v1/system/data-quality")
def data_quality(severity: str | None = None, symbol: str | None = None, limit: int = Query(200, le=2000), db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    q = select(DataQualityLog, Stock.symbol).outerjoin(Stock, Stock.id == DataQualityLog.stock_id)
    if severity:
        q = q.where(DataQualityLog.severity == severity.upper())
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    rows = db.execute(q.order_by(DataQualityLog.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "symbol": sym, "check_type": r.check_type, "severity": r.severity, "trade_date": r.trade_date, "message": r.message, "details": r.details, "provider": r.provider, "created_at": r.created_at} for r, sym in rows]


@router.get("/api/v1/system/logs")
def logs(limit: int = Query(200, le=2000), db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    rows = db.scalars(select(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "level": r.level, "component": r.component, "event": r.event, "message": r.message, "details": r.details, "created_at": r.created_at} for r in rows]


JOBS = {
    "daily_update": lambda: jobs.run_daily_update(),
    "fundamentals_update": lambda: jobs.run_fundamentals_update(),
    "daily_report": lambda: jobs.run_report("daily"),
    "weekly_report": lambda: jobs.run_report("weekly"),
    "monthly_report": lambda: jobs.run_report("monthly"),
    "dispatch_notifications": lambda: jobs.run_dispatch(),
}


@router.post("/api/v1/system/jobs/{job}/run")
def run_job(job: str, background: BackgroundTasks, wait: bool = False, _a: User = Depends(require_admin)):
    fn = JOBS.get(job)
    if not fn:
        raise HTTPException(404, f"unknown job; available: {sorted(JOBS)}")
    if wait:
        return {"job": job, "result": fn()}
    background.add_task(fn)
    return {"job": job, "status": "started"}
