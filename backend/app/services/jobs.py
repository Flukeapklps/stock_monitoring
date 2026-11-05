"""Pipeline jobs shared by the scheduler, admin API and LINE bot.

Each job opens its own DB session and uses a Redis lock so the same job never
runs concurrently in the API and worker processes.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select

from app.analysis import fundamentals as fund
from app.analysis.service import analyze_stocks, default_benchmark
from app.core.cache import acquire_once, backend
from app.core.config import get_settings
from app.db.session import session_scope
from app.market import calendar
from app.models import Stock, Watchlist, WatchlistItem
from app.pipeline import repository as repo
from app.pipeline.ingestion import update_symbols

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg-job")
_last_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _record(job: str, status: str, details: dict | None = None) -> None:
    with _lock:
        _last_runs[job] = {"job": job, "status": status, "at": datetime.now(UTC).isoformat(), "details": details or {}}
    try:
        import json

        backend().set(f"job:last:{job}", json.dumps(_last_runs[job], default=str), ttl=30 * 86400)
    except Exception:  # pragma: no cover
        pass


def last_runs() -> dict[str, dict]:
    import json

    out = {}
    for job in ("daily_update", "weekly_update", "monthly_update", "fundamentals_update", "dispatch_notifications", "daily_report", "weekly_report", "monthly_report"):
        try:
            raw = backend().get(f"job:last:{job}")
        except Exception:  # pragma: no cover
            raw = None
        if raw:
            out[job] = json.loads(raw)
        elif job in _last_runs:
            out[job] = _last_runs[job]
    return out


def tracked_universe(db) -> list[str]:
    """Universe = configured default + benchmarks + market overview + every watched/held symbol."""
    s = get_settings()
    syms = set(s.default_universe_list) | set(s.benchmark_list) | set(s.market_overview_list)
    syms |= set(db.scalars(select(Stock.symbol).where(Stock.is_tracked.is_(True))))
    return sorted(syms)


def run_daily_update(*, now: datetime | None = None, send_alerts: bool = True, symbols: list[str] | None = None) -> dict:
    """Prices -> validation -> indicators -> signals -> alerts."""
    if not acquire_once("lock:daily_update", ttl=3600):
        return {"status": "SKIPPED", "reason": "already running"}
    try:
        with session_scope() as db:
            universe = symbols or tracked_universe(db)
            rep = update_symbols(db, universe, now=now)
            stocks = list(db.scalars(select(Stock).where(Stock.symbol.in_(universe), Stock.asset_type != "INDEX")))
            results = analyze_stocks(db, stocks, now=now, benchmark=default_benchmark())
            queued = 0
            if send_alerts:
                from app.integrations.line import alerts

                queued += alerts.alerts_from_analysis(db, results)
                queued += alerts.price_alerts(db, now)
                queued += alerts.portfolio_alerts(db)
            summary = {"ingestion": rep.summary()["counts"], "analyzed": sum(1 for r in results if r.status == "ANALYZED"), "stale": [r.symbol for r in results if r.status == "STALE"], "alerts_queued": queued, "latest_session": rep.latest_session.isoformat()}
            repo.system_log(db, "INFO", "scheduler", "daily_update", "completed", summary)
        _record("daily_update", "OK", summary)
        return summary
    except Exception as exc:
        log.exception("daily update failed")
        _record("daily_update", "ERROR", {"error": str(exc)[:300]})
        raise
    finally:
        backend().delete("lock:daily_update")


def run_fundamentals_update(send_alerts: bool = True) -> dict:
    if not acquire_once("lock:fundamentals", ttl=3600):
        return {"status": "SKIPPED"}
    try:
        out = []
        with session_scope() as db:
            stocks = list(db.scalars(select(Stock).where(Stock.is_tracked.is_(True), Stock.asset_type == "EQUITY")))
            for s in stocks:
                out.append(fund.refresh_fundamentals(db, s))
                db.commit()
            if send_alerts:
                from app.integrations.line import alerts

                alerts.filing_alerts(db)
                alerts.deterioration_alerts(db, stocks)
        summary = {"results": out}
        _record("fundamentals_update", "OK", {"count": len(out)})
        return summary
    finally:
        backend().delete("lock:fundamentals")


def run_report(kind: str, session=None) -> dict:
    from app.integrations.line import reports

    with session_scope() as db:
        n = reports.send_reports(db, kind, session)
    _record(f"{kind}_report", "OK", {"queued": n})
    return {"queued": n}


def run_dispatch() -> dict:
    from app.integrations.line.notification_service import dispatch_pending, reset_stuck_sending

    with session_scope() as db:
        reset_stuck_sending(db)
        stats = dispatch_pending(db)
    if any(v for k, v in stats.items() if k != "note"):
        _record("dispatch_notifications", "OK", stats)
    return stats


def submit_symbol_load(symbol: str) -> None:
    """Background initial load for a newly added symbol (from web or LINE)."""

    def _run():
        try:
            with session_scope() as db:
                update_symbols(db, [symbol])
                st = repo.get_stock(db, symbol)
                if st:
                    analyze_stocks(db, [st], benchmark=default_benchmark())
                    if st.asset_type == "EQUITY":
                        fund.refresh_fundamentals(db, st)
        except Exception:
            log.exception("background load failed for %s", symbol)

    if get_settings().environment == "test":
        return
    _executor.submit(_run)


def default_watchlist(db, user_id: int) -> Watchlist:
    wl = db.scalar(select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.is_default.is_(True)))
    if wl is None:
        wl = db.scalar(select(Watchlist).where(Watchlist.user_id == user_id).order_by(Watchlist.id))
    if wl is None:
        wl = Watchlist(user_id=user_id, name="My Watchlist", is_default=True)
        db.add(wl)
        db.flush()
    return wl


def add_to_watchlist(db, wl: Watchlist, stock: Stock) -> bool:
    exists = db.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.stock_id == stock.id))
    if exists:
        return False
    db.add(WatchlistItem(watchlist_id=wl.id, stock_id=stock.id, added_at=datetime.now(UTC)))
    stock.is_tracked = True
    db.flush()
    return True


def is_report_day(kind: str, d=None) -> bool:
    d = d or calendar.latest_completed_session()
    if kind == "weekly":
        return calendar.is_last_session_of_week(d)
    if kind == "monthly":
        return calendar.is_last_session_of_month(d)
    return True

