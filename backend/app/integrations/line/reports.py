"""Daily / weekly / monthly LINE reports (personalised per recipient)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.line import flex_templates as ft
from app.integrations.line import message_builder as mb
from app.integrations.line.notification_service import enqueue, followed_symbols, recipients_for
from app.market import calendar
from app.models import InvestmentScore, InvestmentSignal, LineUser, MarketDataWeekly, Portfolio, SecFiling, Stock
from app.pipeline.quality import freshness
from app.portfolio.service import evaluate_portfolio, performance_series
from app.services.views import stock_rows

INDEX_NAMES = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^DJI": "Dow Jones", "^RUT": "Russell 2000", "^VIX": "VIX", "SPY": "SPY", "QQQ": "QQQ"}


def _stocks(db: Session, symbols) -> list[Stock]:
    return list(db.scalars(select(Stock).where(Stock.symbol.in_(list(symbols))))) if symbols else []


def _index_rows(db: Session) -> list[dict]:
    syms = get_settings().market_overview_list
    rows = stock_rows(db, _stocks(db, syms))
    order = {s: i for i, s in enumerate(syms)}
    return sorted(rows, key=lambda r: order.get(r["symbol"], 99))


def _links(path: str) -> list[tuple[str, str]]:
    return [("Open Dashboard", f"{get_settings().public_base_url.rstrip('/')}{path}")]


def daily_report(db: Session, lu: LineUser, session: date) -> list[dict]:
    syms = followed_symbols(db, lu)
    idx = _index_rows(db)
    rows = [(INDEX_NAMES.get(r["symbol"], r["symbol"]), f"{r['price']:,.2f} {ft.pct(r['change_pct'])}" if r["price"] is not None else "n/a", ft.change_color(r["change_pct"])) for r in idx if r["symbol"] in ("^GSPC", "^IXIC", "^DJI")]
    wl = stock_rows(db, _stocks(db, syms))
    wl_sorted = sorted([r for r in wl if r["change_pct"] is not None], key=lambda r: -r["change_pct"])
    perf = [f"{r['symbol']} {ft.money(r['price'])} {ft.pct(r['change_pct'])}" for r in wl_sorted[:8]]
    ids = [s.id for s in _stocks(db, syms)]
    sigs = db.execute(select(InvestmentSignal, Stock.symbol).join(Stock, Stock.id == InvestmentSignal.stock_id).where(InvestmentSignal.stock_id.in_(ids), InvestmentSignal.signal_date == session)).all() if ids else []
    new_sigs = [f"{sym}: {s.signal_type.replace('_', ' ')}" + (f" ({s.score:.0f})" if s.score is not None else "") for s, sym in sigs]
    moves = [f"{r['symbol']} {ft.pct(r['change_pct'])}" for r in wl if r["change_pct"] is not None and abs(r["change_pct"]) >= 3]
    stale = [r["symbol"] for r in wl + idx if r["freshness"]["status"] != "FRESH"]
    fresh_line = "All followed symbols are up to date." if not stale else f"Stale/unavailable: {', '.join(sorted(set(stale))[:10])}"
    card = ft.table_card(
        f"📊 Daily Market Report · {session.isoformat()}",
        "US close · END_OF_DAY data",
        rows,
        sections=[("Watchlist performance", perf or ["No watchlist symbols."]), ("New signals", new_sigs), ("Important moves (≥3%)", moves), ("Data freshness", [fresh_line])],
        footer_note="Automated summary, not investment advice.",
        links=_links("/"),
    )
    return mb.validated([ft.flex(f"Daily market report {session.isoformat()}", card)])


def _weekly_return(db: Session, stock_id: int, week_end: date) -> float | None:
    rows = db.scalars(select(MarketDataWeekly).where(MarketDataWeekly.stock_id == stock_id, MarketDataWeekly.trade_date <= week_end).order_by(MarketDataWeekly.trade_date.desc()).limit(2)).all()
    if len(rows) < 2 or rows[0].trade_date < week_end - timedelta(days=6):
        return None
    a = float(rows[0].adj_close or rows[0].close)
    b = float(rows[1].adj_close or rows[1].close)
    return (a / b - 1) * 100 if b else None


def weekly_report(db: Session, lu: LineUser, week_end: date) -> list[dict]:
    syms = followed_symbols(db, lu)
    idx = _stocks(db, [s for s in get_settings().market_overview_list if s != "^VIX"])
    rows = []
    for s in idx:
        wr = _weekly_return(db, s.id, week_end)
        rows.append((INDEX_NAMES.get(s.symbol, s.symbol), ft.pct(wr), ft.change_color(wr)))
    stocks = _stocks(db, syms)
    perf = [(s.symbol, _weekly_return(db, s.id, week_end)) for s in stocks]
    perf = [p for p in perf if p[1] is not None]
    perf.sort(key=lambda x: -x[1])
    strongest = [f"{s} {ft.pct(v)}" for s, v in perf[:3]]
    weakest = [f"{s} {ft.pct(v)}" for s, v in perf[-3:][::-1]] if len(perf) > 3 else []
    wl = stock_rows(db, stocks)
    near200 = [f"{r['symbol']} {r['dist_sma_200_pct']:+.1f}% from SMA200" for r in wl if r["dist_sma_200_pct"] is not None and abs(r["dist_sma_200_pct"]) <= 3]
    week_start = week_end - timedelta(days=6)
    ids = [s.id for s in stocks]
    sigs = db.execute(select(InvestmentSignal, Stock.symbol).join(Stock, Stock.id == InvestmentSignal.stock_id).where(InvestmentSignal.stock_id.in_(ids), InvestmentSignal.signal_date >= week_start, InvestmentSignal.signal_date <= week_end)).all() if ids else []
    accum = sorted({sym for s, sym in sigs if s.signal_type == "ACCUMULATION_WATCH"})
    weakening = sorted({sym for s, sym in sigs if s.signal_type in ("TREND_WEAKENING", "WEAKENING", "BEARISH")})
    port_lines = []
    if lu.user_id:
        for p in db.scalars(select(Portfolio).where(Portfolio.user_id == lu.user_id)):
            series = performance_series(db, p, benchmark=None)["series"]
            pts = [x for x in series if x["date"] <= week_end.isoformat()]
            prev = [x for x in pts if x["date"] <= (week_end - timedelta(days=7)).isoformat()]
            if pts and prev:
                port_lines.append(f"{p.name}: {(pts[-1]['index'] / prev[-1]['index'] - 1) * 100:+.2f}% this week")
    card = ft.table_card(
        f"🗓 Weekly Report · week ending {week_end.isoformat()}",
        "Weekly performance (total return)",
        rows,
        sections=[
            ("Strongest watchlist stocks", strongest),
            ("Weakest watchlist stocks", weakest),
            ("Approaching SMA 200 (±3%)", near200),
            ("Accumulation opportunities", accum),
            ("Weakening trends", weakening),
            ("Portfolio", port_lines),
        ],
        footer_note="Automated summary, not investment advice.",
        links=_links("/signals"),
    )
    return mb.validated([ft.flex(f"Weekly report {week_end.isoformat()}", card)])


def monthly_report(db: Session, lu: LineUser, month_end: date) -> list[dict]:
    month_start = month_end.replace(day=1)
    lines_port: list[str] = []
    if lu.user_id:
        for p in db.scalars(select(Portfolio).where(Portfolio.user_id == lu.user_id)):
            perf = performance_series(db, p, benchmark="SPY")
            pts = [x for x in perf["series"] if x["date"] <= month_end.isoformat()]
            prev = [x for x in pts if x["date"] < month_start.isoformat()]
            if pts and prev:
                r = (pts[-1]["index"] / prev[-1]["index"] - 1) * 100
                line = f"{p.name}: {r:+.2f}%"
                b_now, b_prev = pts[-1].get("benchmark_index"), prev[-1].get("benchmark_index")
                if b_now and b_prev:
                    line += f" vs SPY {(b_now / b_prev - 1) * 100:+.2f}%"
                lines_port.append(line)
            ev = evaluate_portfolio(db, p)
            if ev["totals"]["unrealized_pl"] is not None:
                lines_port.append(f"{p.name} unrealized P/L {ft.money(ev['totals']['unrealized_pl'])}")
    syms = followed_symbols(db, lu)
    stocks = _stocks(db, syms)
    ids = [s.id for s in stocks]
    changes = []
    score_changes = []
    for s in stocks:
        prim = db.scalars(select(InvestmentSignal).where(InvestmentSignal.stock_id == s.id, InvestmentSignal.is_primary.is_(True), InvestmentSignal.signal_date <= month_end).order_by(InvestmentSignal.signal_date.desc()).limit(40)).all()
        before = [x for x in prim if x.signal_date < month_start]
        now_ = [x for x in prim if x.signal_date >= month_start]
        if before and now_ and before[0].signal_type != now_[0].signal_type:
            changes.append(f"{s.symbol}: {before[0].signal_type.replace('_', ' ')} → {now_[0].signal_type.replace('_', ' ')}")
        scs = db.scalars(select(InvestmentScore).where(InvestmentScore.stock_id == s.id, InvestmentScore.score_date <= month_end).order_by(InvestmentScore.score_date.desc()).limit(40)).all()
        sb = [x for x in scs if x.score_date < month_start]
        sn = [x for x in scs if x.score_date >= month_start]
        if sb and sn and sb[0].total_score is not None and sn[0].total_score is not None and abs(sn[0].total_score - sb[0].total_score) >= 5:
            score_changes.append(f"{s.symbol}: {sb[0].total_score:.0f} → {sn[0].total_score:.0f}")
    filings = db.execute(select(SecFiling, Stock.symbol).join(Stock, Stock.id == SecFiling.stock_id).where(SecFiling.stock_id.in_(ids), SecFiling.filing_date >= month_start, SecFiling.filing_date <= month_end, SecFiling.form.in_(("10-K", "10-Q")))).all() if ids else []
    fund_lines = [f"{sym}: {f.form} filed {f.filing_date.isoformat()}" for f, sym in filings]
    card = ft.simple_card(
        f"📅 Monthly Report · {month_start.strftime('%B %Y')}",
        (lines_port or ["No dated portfolio positions."]) + ["— Long-term trend changes —"] + (changes or ["None"]) + ["— Score changes (±5) —"] + (score_changes or ["None"]) + ["— Fundamental developments —"] + (fund_lines or ["None"]),
        footer_note="Automated summary, not investment advice.",
        links=_links("/portfolio"),
    )
    return mb.validated([ft.flex(f"Monthly report {month_start.strftime('%B %Y')}", card)])


def send_reports(db: Session, kind: str, session: date | None = None, only: LineUser | None = None) -> int:
    """Queue a report for each opted-in follower. Idempotent per period (dedupe key)."""
    session = session or calendar.latest_completed_session()
    ntype = {"daily": "DAILY_SUMMARY", "weekly": "WEEKLY_SUMMARY", "monthly": "MONTHLY_SUMMARY"}[kind]
    builder = {"daily": daily_report, "weekly": weekly_report, "monthly": monthly_report}[kind]
    users = [only] if only else recipients_for(db, ntype)
    n = 0
    for lu in users:
        msgs = builder(db, lu, session)
        if enqueue(db, lu, ntype, msgs, dedupe_key=f"{ntype}:{session.isoformat()}", title=f"{kind} report {session.isoformat()}", respect_preferences=only is None):
            n += 1
    return n


def data_freshness_line(db: Session, stock: Stock) -> str:
    from app.pipeline import repository as repo

    return freshness(repo.latest_bar_date(db, stock.id))["status"]
