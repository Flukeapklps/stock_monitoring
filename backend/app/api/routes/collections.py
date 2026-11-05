"""Watchlists, signal center, market overview."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import get_current_user
from app.db.session import get_db
from app.market.calendar import market_status
from app.models import InvestmentSignal, Stock, User, Watchlist, WatchlistItem
from app.pipeline import repository as repo
from app.pipeline.ingestion import InvalidSymbol, ValidationUnavailable, ensure_stock
from app.providers.base import ProviderError
from app.providers.registry import get_provider
from app.services import jobs
from app.services.views import signal_dict, stock_rows

router = APIRouter(prefix="/api/v1", tags=["watchlists", "signals", "market"])

INDEX_NAMES = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ Composite", "^DJI": "Dow Jones Industrial Average", "^RUT": "Russell 2000", "^VIX": "CBOE Volatility Index", "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ (Nasdaq-100)"}


# --- market ----------------------------------------------------------------------------------
@router.get("/market/overview")
def market_overview(db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    syms = get_settings().market_overview_list
    stocks = list(db.scalars(select(Stock).where(Stock.symbol.in_(syms))))
    rows = {r["symbol"]: r for r in stock_rows(db, stocks)}
    items = []
    for s in syms:
        r = rows.get(s)
        if r:
            r = {**r, "display_name": INDEX_NAMES.get(s, r.get("name") or s)}
            items.append(r)
        else:
            items.append({"symbol": s, "display_name": INDEX_NAMES.get(s, s), "price": None, "data_status": "UNAVAILABLE", "note": "not loaded yet"})
    return {"market": market_status(), "items": items}


@router.get("/market/macro")
def macro(_u: User = Depends(get_current_user)):
    p = get_provider("fred")
    if not p.is_configured():
        return {"configured": False, "series": {}}
    try:
        return {"configured": True, "series": p.latest(), "provider": "FRED"}  # type: ignore[attr-defined]
    except ProviderError as exc:
        return {"configured": True, "error": str(exc)[:200], "series": {}}


# --- watchlists ------------------------------------------------------------------------------
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    is_default: bool = False


class ItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(None, max_length=500)


def _own_watchlist(db: Session, user: User, wid: int) -> Watchlist:
    wl = db.get(Watchlist, wid)
    if not wl or wl.user_id != user.id:
        raise HTTPException(404, "watchlist not found")
    return wl


@router.get("/watchlists")
def list_watchlists(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wls = list(db.scalars(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id)))
    if not wls:
        wls = [jobs.default_watchlist(db, user.id)]
        db.commit()
    return [{"id": w.id, "name": w.name, "description": w.description, "is_default": w.is_default, "count": len(w.items)} for w in wls]


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.scalar(select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.name == body.name)):
        raise HTTPException(409, "a watchlist with this name exists")
    wl = Watchlist(user_id=user.id, name=body.name, description=body.description, is_default=body.is_default)
    db.add(wl)
    db.commit()
    return {"id": wl.id, "name": wl.name}


@router.put("/watchlists/{wid}")
def update_watchlist(wid: int, body: WatchlistIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _own_watchlist(db, user, wid)
    wl.name, wl.description = body.name, body.description
    if body.is_default:
        for other in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id)):
            other.is_default = other.id == wl.id
    db.commit()
    return {"id": wl.id}


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _own_watchlist(db, user, wid)
    db.delete(wl)
    db.commit()
    return {"deleted": wid}


@router.get("/watchlists/{wid}")
def watchlist_detail(wid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _own_watchlist(db, user, wid)
    stocks = [it.stock for it in wl.items]
    return {"id": wl.id, "name": wl.name, "description": wl.description, "rows": stock_rows(db, stocks)}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: ItemIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _own_watchlist(db, user, wid)
    try:
        st = ensure_stock(db, body.symbol)
    except InvalidSymbol as exc:
        raise HTTPException(422, str(exc)) from None
    except ValidationUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    added = jobs.add_to_watchlist(db, wl, st)
    db.commit()
    loading = repo.latest_bar_date(db, st.id) is None
    if loading:
        jobs.submit_symbol_load(st.symbol)
    return {"symbol": st.symbol, "added": added, "loading": loading}


@router.delete("/watchlists/{wid}/items/{symbol}")
def remove_item(wid: int, symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    wl = _own_watchlist(db, user, wid)
    st = repo.get_stock(db, symbol)
    if not st:
        raise HTTPException(404, "not in watchlist")
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.stock_id == st.id))
    if not item:
        raise HTTPException(404, "not in watchlist")
    db.delete(item)
    db.commit()
    return {"removed": st.symbol}


# --- signal center ----------------------------------------------------------------------------
@router.get("/signals")
def signal_center(
    signal_type: str | None = None,
    symbol: str | None = None,
    since: date | None = None,
    primary_only: bool = False,
    confirmation: str | None = None,
    limit: int = Query(200, le=2000),
    db: Session = Depends(get_db),
    _u: User = Depends(get_current_user),
):
    q = select(InvestmentSignal, Stock).join(Stock, Stock.id == InvestmentSignal.stock_id)
    if signal_type:
        q = q.where(InvestmentSignal.signal_type.in_([t.strip().upper() for t in signal_type.split(",")]))
    if symbol:
        q = q.where(Stock.symbol == repo.normalize_symbol(symbol))
    if since:
        q = q.where(InvestmentSignal.signal_date >= since)
    if primary_only:
        q = q.where(InvestmentSignal.is_primary.is_(True))
    if confirmation:
        q = q.where(InvestmentSignal.confirmation_status == confirmation.upper())
    rows = db.execute(q.order_by(InvestmentSignal.signal_date.desc(), InvestmentSignal.score.desc().nulls_last()).limit(limit)).all()
    return [signal_dict(s, st.symbol, st.name) for s, st in rows]


@router.get("/signals/summary")
def signal_summary(db: Session = Depends(get_db), _u: User = Depends(get_current_user)):
    """Latest primary signal per stock, grouped by type."""
    q = select(InvestmentSignal, Stock).join(Stock, Stock.id == InvestmentSignal.stock_id).where(InvestmentSignal.is_primary.is_(True)).order_by(InvestmentSignal.stock_id, InvestmentSignal.signal_date.desc()).ext(distinct_on(InvestmentSignal.stock_id))
    groups: dict[str, list] = {k: [] for k in ("STRONG_LONG_TERM_TREND", "BULLISH", "ACCUMULATION_WATCH", "NEUTRAL", "WEAKENING", "BEARISH")}
    for s, st in db.execute(q).all():
        groups.setdefault(s.signal_type, []).append(signal_dict(s, st.symbol, st.name))
    for v in groups.values():
        v.sort(key=lambda x: -(x["score"] or 0))
    return groups
