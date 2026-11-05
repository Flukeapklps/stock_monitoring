from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.models import Portfolio, PortfolioHolding, PortfolioTransaction, User
from app.pipeline import repository as repo
from app.pipeline.ingestion import InvalidSymbol, ValidationUnavailable, ensure_stock
from app.portfolio.service import PortfolioError, add_transaction, evaluate_portfolio, performance_series, rebuild_holding_from_transactions
from app.services import jobs

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    description: str | None = Field(None, max_length=500)
    max_drawdown_alert_pct: float | None = Field(None, gt=0, le=100)
    max_position_pct: float | None = Field(None, gt=0, le=100)


class HoldingIn(BaseModel):
    symbol: str
    quantity: float = Field(gt=0)
    average_cost: float = Field(gt=0)
    purchase_date: date | None = None
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(None, max_length=1000)


class TransactionIn(BaseModel):
    symbol: str
    tx_type: str = Field(pattern=r"^(BUY|SELL|DIVIDEND)$")
    trade_date: date
    quantity: float = Field(ge=0)
    price: float = Field(ge=0)
    fees: float = Field(0, ge=0)
    notes: str | None = Field(None, max_length=1000)


def _own(db: Session, user: User, pid: int) -> Portfolio:
    p = db.get(Portfolio, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "portfolio not found")
    return p


def _stock(db: Session, symbol: str):
    try:
        st = ensure_stock(db, symbol)
    except InvalidSymbol as exc:
        raise HTTPException(422, str(exc)) from None
    except ValidationUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    st.is_tracked = True
    if repo.latest_bar_date(db, st.id) is None:
        jobs.submit_symbol_load(st.symbol)
    return st


@router.get("")
def list_portfolios(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = []
    for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)):
        ev = evaluate_portfolio(db, p)
        out.append({"id": p.id, "name": p.name, "base_currency": p.base_currency, "description": p.description, "totals": ev["totals"], "holdings_count": len(ev["holdings"])})
    return out


@router.post("")
def create_portfolio(body: PortfolioIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.scalar(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.name == body.name)):
        raise HTTPException(409, "portfolio name already used")
    p = Portfolio(user_id=user.id, **body.model_dump())
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.put("/{pid}")
def update_portfolio(pid: int, body: PortfolioIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return {"id": p.id}


@router.delete("/{pid}")
def delete_portfolio(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    db.delete(p)
    db.commit()
    return {"deleted": pid}


@router.get("/{pid}")
def portfolio_detail(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    out = evaluate_portfolio(db, p)
    out["settings"] = {"max_drawdown_alert_pct": float(p.max_drawdown_alert_pct) if p.max_drawdown_alert_pct else None, "max_position_pct": float(p.max_position_pct) if p.max_position_pct else None, "description": p.description}
    return out


@router.get("/{pid}/performance")
def portfolio_performance(pid: int, benchmark: str = "SPY", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return performance_series(db, _own(db, user, pid), benchmark)


@router.post("/{pid}/holdings")
def add_holding(pid: int, body: HoldingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    st = _stock(db, body.symbol)
    if db.scalar(select(PortfolioTransaction.id).where(PortfolioTransaction.portfolio_id == p.id, PortfolioTransaction.stock_id == st.id).limit(1)):
        raise HTTPException(409, f"{st.symbol} is managed by transactions in this portfolio; add a BUY transaction instead")
    h = db.scalar(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == p.id, PortfolioHolding.stock_id == st.id))
    if h is None:
        h = PortfolioHolding(portfolio_id=p.id, stock_id=st.id, quantity=0, average_cost=0)
        db.add(h)
    h.quantity, h.average_cost, h.purchase_date, h.currency, h.notes = body.quantity, body.average_cost, body.purchase_date, body.currency, body.notes
    db.commit()
    return {"holding_id": h.id, "symbol": st.symbol}


@router.delete("/{pid}/holdings/{holding_id}")
def delete_holding(pid: int, holding_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    h = db.get(PortfolioHolding, holding_id)
    if not h or h.portfolio_id != p.id:
        raise HTTPException(404, "holding not found")
    for tx in db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id, PortfolioTransaction.stock_id == h.stock_id)):
        db.delete(tx)
    db.delete(h)
    db.commit()
    return {"deleted": holding_id}


@router.get("/{pid}/transactions")
def list_transactions(pid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    rows = db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id).order_by(PortfolioTransaction.trade_date.desc(), PortfolioTransaction.id.desc())).all()
    return [{"id": t.id, "symbol": t.stock.symbol, "tx_type": t.tx_type, "trade_date": t.trade_date.isoformat(), "quantity": float(t.quantity), "price": float(t.price), "fees": float(t.fees), "currency": t.currency, "notes": t.notes} for t in rows]


@router.post("/{pid}/transactions")
def create_transaction(pid: int, body: TransactionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    st = _stock(db, body.symbol)
    try:
        tx = add_transaction(db, p, st, body.tx_type, body.trade_date, body.quantity, body.price, body.fees, body.notes)
    except PortfolioError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    db.commit()
    return {"id": tx.id}


@router.delete("/{pid}/transactions/{tx_id}")
def delete_transaction(pid: int, tx_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _own(db, user, pid)
    tx = db.get(PortfolioTransaction, tx_id)
    if not tx or tx.portfolio_id != p.id:
        raise HTTPException(404, "transaction not found")
    sid = tx.stock_id
    db.delete(tx)
    db.flush()
    try:
        rebuild_holding_from_transactions(db, p, sid)
    except PortfolioError as exc:
        db.rollback()
        raise HTTPException(422, f"cannot delete: {exc}") from None
    db.commit()
    return {"deleted": tx_id}
