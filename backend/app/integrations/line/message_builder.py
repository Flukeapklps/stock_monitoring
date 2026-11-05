"""Build LINE messages from platform data. Every builder returns a list of
message objects that passes `validator.validate_messages`."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import fundamentals as fund
from app.analysis.service import AnalysisResult
from app.core.config import get_settings
from app.integrations.line import flex_templates as ft
from app.integrations.line.validator import ensure_valid
from app.models import InvestmentScore, InvestmentSignal, Stock
from app.providers.registry import PROVIDER_LABELS
from app.services.views import score_dict, stock_rows

HELP_TEXT = """📊 Stock Monitor — commands
/market – US market overview
/watchlist – your watchlist
/stock AAPL – price & key indicators
/signal AAPL – current technical signals
/score AAPL – long-term investment score
/watch AAPL – add to watchlist
/unwatch AAPL – remove from watchlist
/portfolio – portfolio summary
/top – highest-ranked stocks
/settings – notification settings
/notify <type> on|off – toggle an alert type
/test – send a test notification
/link CODE – link this LINE account to your web account

Commands are case-insensitive. Data is end-of-day; this bot never trades and its alerts are not investment advice."""

WELCOME_TEXT = """Welcome to Stock Monitor 📈

I send long-term technical & fundamental alerts for US stocks and ETFs on your watchlist:
• Accumulation opportunities
• Strong bullish trends
• Trend weakening warnings
• Daily / weekly market reports

Start with /watch AAPL, then try /stock AAPL. Type /help for all commands.

Alerts are automated analysis based on end-of-day data, not guaranteed investment recommendations."""


def _links(symbol: str | None = None) -> list[tuple[str, str]]:
    base = get_settings().public_base_url.rstrip("/")
    out = []
    if symbol:
        out.append(("View Stock Details ↗", f"{base}/stocks/{symbol}"))
        out.append(("View Chart", f"{base}/stocks/{symbol}?tab=chart"))
    out.append(("View Watchlist", f"{base}/watchlist"))
    return out


def _now_str(tz: str = "America/New_York") -> str:
    try:
        z = ZoneInfo(tz)
    except Exception:
        z = ZoneInfo("America/New_York")
    return datetime.now(UTC).astimezone(z).strftime("%Y-%m-%d %H:%M %Z")


def _macd_state(daily: dict | None) -> str:
    if not daily or daily.get("macd") is None or daily.get("macd_signal") is None:
        return "n/a"
    if daily["macd"] > daily["macd_signal"]:
        return "Bullish" + (" (improving)" if daily.get("macd_hist_rising") else "")
    return "Bearish" + (" (improving)" if daily.get("macd_hist_rising") else "")


def text_message(t: str) -> dict:
    return {"type": "text", "text": t[:5000]}


def validated(messages: list[dict]) -> list[dict]:
    ensure_valid(messages)
    return messages


# --- alerts --------------------------------------------------------------------
def alert_payload(stock: Stock, row: dict, signal: str, reasons: list[str], score: float | None, daily: dict | None, *, tz: str = "America/New_York", timeframe: str = "DAILY") -> dict:
    return {
        "symbol": stock.symbol,
        "company": stock.name,
        "signal": signal,
        "price": row.get("price"),
        "change_pct": row.get("change_pct"),
        "trade_date": row.get("trade_date"),
        "score": score,
        "trend": row.get("trend"),
        "rsi": (daily or {}).get("rsi_14", row.get("rsi_14")),
        "macd_state": _macd_state(daily),
        "sma50": (daily or {}).get("sma_50", row.get("sma_50")),
        "sma200": (daily or {}).get("sma_200", row.get("sma_200")),
        "return_1y": (daily or {}).get("return_1y", row.get("return_1y")),
        "reasons": reasons,
        "data_status": row.get("data_status"),
        "provider_label": row.get("provider_label"),
        "generated_at": _now_str(tz),
        "timeframe": timeframe,
        "links": _links(stock.symbol),
    }


def clean_reasons(reasons: list[str]) -> list[str]:
    """Readable bullet text: drop detail parentheses for compact cards, keep score line."""
    out = []
    for r in reasons:
        out.append(r if r.startswith("Investment score") else r.split(" (")[0] + ".")
    return out


def alert_from_analysis(db: Session, res: AnalysisResult, signal: str, *, tz: str = "America/New_York") -> list[dict]:
    stock = db.get(Stock, res.stock_id)
    row = stock_rows(db, [stock])[0]
    sig = db.scalar(select(InvestmentSignal).where(InvestmentSignal.stock_id == stock.id, InvestmentSignal.signal_type == signal).order_by(InvestmentSignal.signal_date.desc()).limit(1))
    reasons = clean_reasons(sig.reasons if sig else [])
    if res.weekly and res.rules and res.rules.weekly_trend:
        reasons.append(f"Weekly trend is {res.rules.weekly_trend.lower()} (confirmation: {sig.confirmation_status if sig else 'n/a'}).")
    payload = alert_payload(stock, row, signal, reasons, res.score.total if res.score else None, res.daily, tz=tz)
    return validated([ft.flex(ft.alt_text_for_alert(payload), ft.stock_alert_bubble(payload))])


def stock_card(db: Session, stock: Stock, *, tz: str = "America/New_York", signal_override: str | None = None) -> list[dict]:
    row = stock_rows(db, [stock])[0]
    if row["price"] is None:
        return validated([text_message(f"{stock.symbol}: no market data stored yet. Data is loaded after the next update (or a few minutes after /watch).")])
    from app.pipeline import repository as repo

    ind = repo.latest_indicators(db, stock.id, "DAILY")
    daily = ind.values if ind else {}
    sig = db.scalar(select(InvestmentSignal).where(InvestmentSignal.stock_id == stock.id, InvestmentSignal.is_primary.is_(True)).order_by(InvestmentSignal.signal_date.desc()).limit(1))
    signal = signal_override or (sig.signal_type if sig else "NEUTRAL")
    reasons = clean_reasons(sig.reasons) if sig else []
    if row["data_status"] == "STALE":
        reasons.insert(0, f"Data is STALE (last bar {row['trade_date']}); signals are paused.")
    payload = alert_payload(stock, row, signal, reasons, row.get("score"), daily, tz=tz)
    return validated([ft.flex(ft.alt_text_for_alert(payload), ft.stock_alert_bubble(payload))])


def signal_message(db: Session, stock: Stock) -> list[dict]:
    sigs = db.scalars(select(InvestmentSignal).where(InvestmentSignal.stock_id == stock.id).order_by(InvestmentSignal.signal_date.desc(), InvestmentSignal.is_primary.desc()).limit(6)).all()
    if not sigs:
        return validated([text_message(f"No signals for {stock.symbol} yet.")])
    latest_date = sigs[0].signal_date
    current = [s for s in sigs if s.signal_date == latest_date]
    lines = []
    for s in current:
        lines.append(f"{s.signal_type.replace('_', ' ')} — {s.confirmation_status.lower()}, score {s.score:.0f}" if s.score is not None else s.signal_type)
        lines += [f"  {r}" for r in clean_reasons(s.reasons)[:4]]
    _i, color, _b, _l = ft.style_for(current[0].signal_type)
    card = ft.simple_card(
        f"{stock.symbol} signals · {latest_date.isoformat()}",
        lines,
        subtitle=f"{stock.name or ''} · DAILY · {current[0].data_status} · {PROVIDER_LABELS.get(current[0].provider, current[0].provider)}",
        color=color,
        footer_note="Technical signals are not investment advice.",
        links=_links(stock.symbol)[:1],
    )
    return validated([ft.flex(f"{stock.symbol} signals", card)])


def score_message(db: Session, stock: Stock) -> list[dict]:
    sc = db.scalar(select(InvestmentScore).where(InvestmentScore.stock_id == stock.id).order_by(InvestmentScore.score_date.desc()).limit(1))
    d = score_dict(sc)
    if not d:
        return validated([text_message(f"No investment score for {stock.symbol} yet.")])
    rows = [("Total", f"{d['total']:.0f}/100" if d["total"] is not None else "n/a", ft.TEXT), ("Classification", (d["classification"] or "n/a").replace("_", " "), ft.TEXT), ("Data coverage", f"{d['coverage'] * 100:.0f}%", ft.YELLOW if d["low_coverage"] else ft.TEXT)]
    for comp in d["components"].values():
        rows.append((f"{comp['label']} ({comp['weight']:.0f}%)", "no data" if comp["score"] is None else f"{comp['score']:.0f}", ft.MUTED if comp["score"] is None else ft.TEXT))
    card = ft.table_card(
        f"{stock.symbol} · Long-Term Investment Score",
        f"As of {d['score_date']}",
        rows,
        sections=[("Why", d["explanation"][:5]), ("Missing data", d["missing"][:5])],
        footer_note=d["disclaimer"],
        links=_links(stock.symbol)[:1],
    )
    return validated([ft.flex(f"{stock.symbol} score {d['total']:.0f}/100" if d["total"] is not None else f"{stock.symbol} score", card)])


def watchlist_message(db: Session, stocks: list[Stock], title: str = "Your watchlist") -> list[dict]:
    if not stocks:
        return validated([text_message("Your watchlist is empty. Add a stock with /watch AAPL")])
    rows = stock_rows(db, stocks)
    tbl = []
    for r in rows[:20]:
        val = f"{ft.money(r['price'])} {ft.pct(r['change_pct'])}"
        if r.get("score") is not None:
            val += f" · {r['score']:.0f}"
        tbl.append((r["symbol"], val, ft.change_color(r["change_pct"])))
    latest = max((r["trade_date"] for r in rows if r["trade_date"]), default="n/a")
    card = ft.table_card(title, f"Close {latest} · END_OF_DAY · price, daily change, score", tbl, links=_links()[-1:])
    return validated([ft.flex(title, card)])


def market_message(db: Session) -> list[dict]:
    syms = get_settings().market_overview_list
    stocks = list(db.scalars(select(Stock).where(Stock.symbol.in_(syms))))
    order = {s: i for i, s in enumerate(syms)}
    stocks.sort(key=lambda s: order.get(s.symbol, 99))
    if not stocks:
        return validated([text_message("Market data not loaded yet.")])
    rows = stock_rows(db, stocks)
    names = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ Comp.", "^DJI": "Dow Jones", "^RUT": "Russell 2000", "^VIX": "VIX"}
    from app.market.calendar import market_status

    ms = market_status()
    tbl = [(names.get(r["symbol"], r["symbol"]), f"{r['price']:,.2f} {ft.pct(r['change_pct'])}" if r["price"] is not None else "n/a", ft.change_color(r["change_pct"])) for r in rows]
    latest = max((r["trade_date"] for r in rows if r["trade_date"]), default="n/a")
    card = ft.table_card("US Market Overview", f"Market {ms['status'].replace('_', ' ').lower()} · last close {latest} · END_OF_DAY", tbl, footer_note="Index levels from end-of-day provider data; not real-time.")
    return validated([ft.flex("US market overview", card)])


def top_message(db: Session, limit: int = 10) -> list[dict]:
    from sqlalchemy import func

    sub = select(InvestmentScore.stock_id, func.max(InvestmentScore.score_date).label("d")).group_by(InvestmentScore.stock_id).subquery()
    q = (
        select(InvestmentScore, Stock)
        .join(sub, (sub.c.stock_id == InvestmentScore.stock_id) & (sub.c.d == InvestmentScore.score_date))
        .join(Stock, Stock.id == InvestmentScore.stock_id)
        .where(InvestmentScore.total_score.is_not(None), Stock.asset_type != "INDEX")
        .order_by(InvestmentScore.total_score.desc())
        .limit(limit)
    )
    rows = db.execute(q).all()
    if not rows:
        return validated([text_message("No scored stocks yet.")])
    tbl = [(f"{i}. {st.symbol}", f"{sc.total_score:.0f} · {(sc.classification or '').replace('_', ' ').title()}", ft.GREEN if sc.total_score >= 65 else (ft.YELLOW if sc.total_score >= 50 else ft.RED)) for i, (sc, st) in enumerate(rows, 1)]
    card = ft.table_card("Top-ranked stocks", "Long-Term Investment Score (tracked universe)", tbl, footer_note="Scores describe current characteristics, not future returns.")
    return validated([ft.flex("Top-ranked stocks", card)])


def settings_message(prefs: dict[str, dict], general: dict) -> list[dict]:
    from app.integrations.line.notification_service import NOTIFICATION_LABELS

    tbl = [(NOTIFICATION_LABELS.get(k, k), "ON" if v["enabled"] else "OFF", ft.GREEN if v["enabled"] else ft.MUTED) for k, v in prefs.items()]
    qh = general.get("quiet_hours") or {}
    extra = [
        f"Minimum score: {general.get('min_score', 0)}",
        f"RSI accumulation zone: {general.get('rsi_low', 40)}-{general.get('rsi_high', 50)}",
        f"Cooldown: {general.get('cooldown_minutes')} min · max {general.get('max_per_day')} alerts/day",
        f"Quiet hours: {qh.get('start', '-')}–{qh.get('end', '-')} ({general.get('timezone')})" if qh else f"Quiet hours: off ({general.get('timezone')})",
        "Change: /notify accumulation off (types: daily, weekly, monthly, bullish, accumulation, weakening, price, fundamental, portfolio)",
    ]
    card = ft.table_card("Notification settings", None, tbl, sections=[("General", extra)], links=[("Edit on dashboard", f"{get_settings().public_base_url.rstrip('/')}/settings/line")])
    return validated([ft.flex("Notification settings", card)])


def portfolio_message(db: Session, portfolios) -> list[dict]:
    from app.portfolio.service import evaluate_portfolio

    if not portfolios:
        return validated([text_message("No portfolio found. Create one on the web dashboard and link your account with /link CODE.")])
    bubbles = []
    for p in portfolios[:5]:
        ev = evaluate_portfolio(db, p)
        t = ev["totals"]
        rows = [
            ("Market value", ft.money(t["market_value"]), ft.TEXT),
            ("Unrealized P/L", f"{ft.money(t['unrealized_pl'])} ({ft.pct(t['return_pct'])})", ft.change_color(t["unrealized_pl"])),
            ("Realized P/L", ft.money(t["realized_pl"]), ft.change_color(t["realized_pl"])),
            ("Dividends", ft.money(t["dividend_income"]), ft.TEXT),
        ]
        for h in ev["holdings"][:8]:
            rows.append((f"{h['symbol']} ({(h['allocation_pct'] or 0):.0f}%)", f"{ft.money(h['market_value'])} {ft.pct(h['return_pct'])}", ft.change_color(h["return_pct"])))
        bubbles.append(ft.table_card(f"💼 {p.name}", "Valued at latest END_OF_DAY close", rows, links=[("View Portfolio", f"{get_settings().public_base_url.rstrip('/')}/portfolio")]))
    contents = bubbles[0] if len(bubbles) == 1 else {"type": "carousel", "contents": bubbles}
    return validated([ft.flex("Portfolio summary", contents)])


def fundamental_alert(stock: Stock, title: str, lines: list[str], link_url: str | None = None) -> list[dict]:
    links = [("View Filing", link_url)] if link_url else []
    links += _links(stock.symbol)[:1]
    card = ft.simple_card(f"📄 {stock.symbol} — {title}", lines, subtitle=stock.name, color=ft.YELLOW, footer_note="Source: SEC EDGAR. Not investment advice.", links=links)
    return validated([ft.flex(f"{stock.symbol}: {title}", card)])


def price_alert(stock: Stock, title: str, lines: list[str], up: bool) -> list[dict]:
    card = ft.simple_card(f"🔔 {stock.symbol} — {title}", lines, subtitle=stock.name, color=ft.GREEN if up else ft.RED, footer_note="End-of-day close. Not investment advice.", links=_links(stock.symbol)[:1])
    return validated([ft.flex(f"{stock.symbol}: {title}", card)])


def fundamental_summary_lines(db: Session, stock: Stock) -> list[str]:
    return fund.fundamental_summary(fund.latest_snapshot(db, stock.id))
