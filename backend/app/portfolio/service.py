"""Portfolio accounting.

Cost basis uses the average-cost method. Holdings can be entered manually
(quantity / average cost / purchase date) or derived from the transaction log;
when a stock has transactions in a portfolio, its holding is rebuilt from them.
Market values use the latest stored daily close (END_OF_DAY), never invented prices.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.indicators.core import drawdown
from app.models import MarketDataDaily, Portfolio, PortfolioHolding, PortfolioTransaction, Stock
from app.pipeline import repository as repo
from app.providers.registry import PROVIDER_LABELS


class PortfolioError(ValueError):
    pass


def rebuild_holding_from_transactions(db: Session, portfolio: Portfolio, stock_id: int) -> PortfolioHolding | None:
    txs = db.scalars(
        select(PortfolioTransaction)
        .where(PortfolioTransaction.portfolio_id == portfolio.id, PortfolioTransaction.stock_id == stock_id)
        .order_by(PortfolioTransaction.trade_date, PortfolioTransaction.id)
    ).all()
    qty = cost = realized = 0.0
    first_buy: date | None = None
    for t in txs:
        q, p, fee = float(t.quantity), float(t.price), float(t.fees or 0)
        if t.tx_type == "BUY":
            cost += q * p + fee
            qty += q
            first_buy = first_buy or t.trade_date
        elif t.tx_type == "SELL":
            if q > qty + 1e-9:
                raise PortfolioError(f"sell quantity {q} exceeds position {qty} on {t.trade_date}")
            avg = cost / qty if qty else 0.0
            realized += q * (p - avg) - fee
            cost -= avg * q
            qty -= q
            if qty <= 1e-9:
                qty, cost, first_buy = 0.0, 0.0, None
        elif t.tx_type == "DIVIDEND":
            realized += q * p if q else p  # quantity * per-share amount, or total amount when quantity is 0
    holding = db.scalar(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id, PortfolioHolding.stock_id == stock_id))
    if holding is None:
        holding = PortfolioHolding(portfolio_id=portfolio.id, stock_id=stock_id, quantity=0, average_cost=0)
        db.add(holding)
    holding.quantity = round(qty, 6)
    holding.average_cost = round(cost / qty, 6) if qty else 0
    holding.realized_pl = round(realized, 6)
    holding.purchase_date = first_buy
    db.flush()
    return holding


def add_transaction(db: Session, portfolio: Portfolio, stock: Stock, tx_type: str, trade_date: date, quantity: float, price: float, fees: float = 0, notes: str | None = None) -> PortfolioTransaction:
    tx_type = tx_type.upper()
    if tx_type not in ("BUY", "SELL", "DIVIDEND"):
        raise PortfolioError("tx_type must be BUY, SELL or DIVIDEND")
    if quantity < 0 or price < 0 or fees < 0:
        raise PortfolioError("quantity, price and fees must be non-negative")
    if tx_type in ("BUY", "SELL") and (quantity <= 0 or price <= 0):
        raise PortfolioError("BUY/SELL require positive quantity and price")
    tx = PortfolioTransaction(portfolio_id=portfolio.id, stock_id=stock.id, tx_type=tx_type, trade_date=trade_date, quantity=quantity, price=price, fees=fees, notes=notes, currency=portfolio.base_currency)
    db.add(tx)
    db.flush()
    try:
        rebuild_holding_from_transactions(db, portfolio, stock.id)
    except PortfolioError:
        db.delete(tx)
        db.flush()
        raise
    return tx


def _dividends_received(db: Session, stock_id: int, qty_by_date, start: date | None) -> float:
    if start is None:
        return 0.0
    rows = db.execute(
        select(MarketDataDaily.trade_date, MarketDataDaily.dividend).where(
            MarketDataDaily.stock_id == stock_id, MarketDataDaily.dividend.is_not(None), MarketDataDaily.trade_date >= start
        )
    ).all()
    total = 0.0
    for d, div in rows:
        total += float(div) * qty_by_date(d)
    return total


def evaluate_portfolio(db: Session, portfolio: Portfolio) -> dict:
    holdings = [h for h in portfolio.holdings if float(h.quantity) > 0 or float(h.realized_pl or 0) != 0]
    has_tx = {sid for (sid,) in db.execute(select(PortfolioTransaction.stock_id).where(PortfolioTransaction.portfolio_id == portfolio.id).distinct())}
    rows = []
    total_mv = total_cost = total_realized = total_div = 0.0
    stale = False
    for h in holdings:
        stock = h.stock
        bar = db.scalar(select(MarketDataDaily).where(MarketDataDaily.stock_id == stock.id).order_by(MarketDataDaily.trade_date.desc()).limit(1))
        qty, avg = float(h.quantity), float(h.average_cost)
        price = float(bar.close) if bar and bar.close is not None else None
        mv = qty * price if price is not None else None
        cost = qty * avg
        unreal = mv - cost if mv is not None else None
        # dividend income (from provider dividend history) for manually-entered holdings;
        # transaction-based holdings record dividends explicitly as DIVIDEND transactions.
        div = 0.0
        if stock.id not in has_tx and h.purchase_date:
            div = _dividends_received(db, stock.id, lambda _d, q=qty: q, h.purchase_date)
        realized = float(h.realized_pl or 0)
        if mv is None:
            stale = True
        total_mv += mv or 0
        total_cost += cost
        total_realized += realized
        total_div += div
        rows.append(
            {
                "holding_id": h.id,
                "symbol": stock.symbol,
                "name": stock.name,
                "quantity": qty,
                "average_cost": avg,
                "purchase_date": h.purchase_date.isoformat() if h.purchase_date else None,
                "currency": h.currency,
                "notes": h.notes,
                "price": price,
                "price_date": bar.trade_date.isoformat() if bar else None,
                "data_status": bar.data_status if bar else "UNAVAILABLE",
                "provider": PROVIDER_LABELS.get(bar.provider, bar.provider) if bar else None,
                "market_value": mv,
                "cost_basis": cost,
                "unrealized_pl": unreal,
                "return_pct": (unreal / cost * 100) if unreal is not None and cost else None,
                "realized_pl": realized,
                "dividend_income": div,
                "from_transactions": stock.id in has_tx,
            }
        )
    for r in rows:
        r["allocation_pct"] = (r["market_value"] / total_mv * 100) if total_mv and r["market_value"] is not None else None
    unreal_total = total_mv - total_cost if not stale else None
    return {
        "portfolio_id": portfolio.id,
        "name": portfolio.name,
        "base_currency": portfolio.base_currency,
        "holdings": sorted(rows, key=lambda r: -(r["market_value"] or 0)),
        "totals": {
            "market_value": total_mv,
            "cost_basis": total_cost,
            "unrealized_pl": unreal_total,
            "return_pct": (unreal_total / total_cost * 100) if unreal_total is not None and total_cost else None,
            "realized_pl": total_realized,
            "dividend_income": total_div,
            "total_pl": (unreal_total + total_realized + total_div) if unreal_total is not None else None,
        },
        "incomplete_prices": stale,
    }


def performance_series(db: Session, portfolio: Portfolio, benchmark: str | None = "SPY") -> dict:
    """Daily portfolio value from position history (transactions, or manual holdings
    from their purchase date). Returns are time-weighted to neutralise cash flows."""
    positions: dict[int, list[tuple[date, float]]] = defaultdict(list)  # stock -> [(date, delta qty)]
    flows: dict[date, float] = defaultdict(float)
    txs = db.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == portfolio.id).order_by(PortfolioTransaction.trade_date)).all()
    tx_stocks = set()
    for t in txs:
        if t.tx_type == "BUY":
            positions[t.stock_id].append((t.trade_date, float(t.quantity)))
            flows[t.trade_date] += float(t.quantity) * float(t.price) + float(t.fees or 0)
            tx_stocks.add(t.stock_id)
        elif t.tx_type == "SELL":
            positions[t.stock_id].append((t.trade_date, -float(t.quantity)))
            flows[t.trade_date] -= float(t.quantity) * float(t.price) - float(t.fees or 0)
            tx_stocks.add(t.stock_id)
    for h in portfolio.holdings:
        if h.stock_id not in tx_stocks and h.purchase_date and float(h.quantity) > 0:
            positions[h.stock_id].append((h.purchase_date, float(h.quantity)))
            flows[h.purchase_date] += float(h.quantity) * float(h.average_cost)
    if not positions:
        return {"series": [], "metrics": {}, "note": "No dated positions (add purchase dates or transactions)."}
    start = min(d for v in positions.values() for d, _ in v)
    value = None
    for sid, deltas in positions.items():
        bars = repo.load_bars(db, "DAILY", sid, start=start - timedelta(days=7))
        if bars.empty:
            continue
        px = bars["close"].astype(float)
        qty = pd.Series(0.0, index=px.index)
        for d, dq in deltas:
            qty[qty.index >= d] += dq
        v = (qty * px).rename(sid)
        value = v.to_frame() if value is None else value.join(v.to_frame(), how="outer")
    if value is None:
        return {"series": [], "metrics": {}, "note": "No price data for holdings."}
    total = value.ffill().fillna(0).sum(axis=1)
    total = total[total.index >= start]
    flow = pd.Series(flows).reindex(total.index, fill_value=0.0)
    # time-weighted daily return: (V_t - CF_t) / V_{t-1} - 1
    prev = total.shift(1)
    r = ((total - flow) / prev - 1).where(prev > 0).fillna(0.0)
    twr = (1 + r).cumprod() * 100
    out_series = [{"date": d.isoformat(), "value": round(float(v), 2), "index": round(float(i), 4)} for d, v, i in zip(total.index, total.values, twr.values, strict=True)]
    metrics = {
        "time_weighted_return_pct": round(float(twr.iloc[-1] - 100), 4) if len(twr) else None,
        "max_drawdown_pct": round(float(drawdown(twr).min()), 4) if len(twr) else None,
        "current_drawdown_pct": round(float(drawdown(twr).iloc[-1]), 4) if len(twr) else None,
        "start_date": start.isoformat(),
    }
    if benchmark:
        b = repo.get_stock(db, benchmark)
        if b:
            bars = repo.load_bars(db, "DAILY", b.id, start=start)
            if not bars.empty:
                adj = bars["adj_close"].fillna(bars["close"]).astype(float)
                bidx = adj / adj.iloc[0] * 100
                metrics["benchmark"] = benchmark
                metrics["benchmark_return_pct"] = round(float(bidx.iloc[-1] - 100), 4)
                bmap = {d.isoformat(): round(float(v), 4) for d, v in bidx.items()}
                for row in out_series:
                    row["benchmark_index"] = bmap.get(row["date"])
    return {"series": out_series, "metrics": metrics}
