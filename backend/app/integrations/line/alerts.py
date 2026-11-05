"""Translate analysis results and rule evaluations into queued LINE notifications.

This module only *reads* analysis output; no LINE logic lives in the indicator
or signal modules.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import fundamentals as fund
from app.analysis.service import AnalysisResult
from app.integrations.line import message_builder as mb
from app.integrations.line.notification_service import enqueue, get_preferences, recipients_for
from app.models import AlertRule, InvestmentSignal, LineAlertRule, LineUser, MarketDataDaily, Portfolio, SecFiling, Stock
from app.portfolio.service import evaluate_portfolio, performance_series

log = logging.getLogger(__name__)

BULLISH = {"STRONG_LONG_TERM_TREND", "BULLISH"}
WEAK = {"WEAKENING", "BEARISH"}


def _user_tz(general: dict) -> str:
    return general.get("timezone") or "America/New_York"


def alerts_from_analysis(db: Session, results: list[AnalysisResult]) -> int:
    """Queue alerts for new signals. Returns number of queued notifications."""
    n = 0
    for res in results:
        if res.status != "ANALYZED" or not res.primary_signal:
            continue
        d = res.trade_date.isoformat()
        candidates: list[tuple[str, str]] = []  # (notification_type, signal_type)
        if res.primary_signal == "ACCUMULATION_WATCH" and res.previous_primary_signal != "ACCUMULATION_WATCH":
            candidates.append(("ACCUMULATION", "ACCUMULATION_WATCH"))
        if res.primary_signal in BULLISH and res.previous_primary_signal not in BULLISH | {"ACCUMULATION_WATCH"}:
            sig = _signal(db, res.stock_id, res.trade_date, res.primary_signal)
            if sig and sig.confirmation_status == "CONFIRMED":  # weekly trend must agree
                candidates.append(("STRONG_BULLISH", res.primary_signal))
        if "TREND_WEAKENING" in res.new_signals and not _had_signal_recently(db, res.stock_id, "TREND_WEAKENING", res.trade_date):
            candidates.append(("TREND_WEAKENING", "TREND_WEAKENING"))
        elif res.primary_signal in WEAK and res.previous_primary_signal not in WEAK and res.previous_primary_signal is not None:
            candidates.append(("TREND_WEAKENING", res.primary_signal))

        for ntype, sig_type in candidates:
            for lu in recipients_for(db, ntype, res.symbol):
                _, general = get_preferences(db, lu)
                score = res.score.total if res.score else None
                if ntype in ("ACCUMULATION", "STRONG_BULLISH") and score is not None and score < float(general.get("min_score") or 0):
                    continue
                if ntype == "ACCUMULATION":
                    rsi = (res.daily or {}).get("rsi_14")
                    if rsi is None or not float(general.get("rsi_low", 40)) <= rsi <= float(general.get("rsi_high", 50)):
                        continue
                msgs = mb.alert_from_analysis(db, res, sig_type, tz=_user_tz(general))
                h = enqueue(db, lu, ntype, msgs, dedupe_key=f"{ntype}:{res.symbol}:{d}", title=f"{res.symbol} {sig_type}", stock_symbol=res.symbol, summary=f"score {score}", context={"signal": sig_type, "trade_date": d})
                n += 1 if h else 0
    return n


def _signal(db: Session, stock_id: int, d: date, sig_type: str) -> InvestmentSignal | None:
    return db.scalar(select(InvestmentSignal).where(InvestmentSignal.stock_id == stock_id, InvestmentSignal.signal_date == d, InvestmentSignal.signal_type == sig_type))


def _had_signal_recently(db: Session, stock_id: int, sig_type: str, d: date, days: int = 10) -> bool:
    return db.scalar(
        select(InvestmentSignal.id).where(InvestmentSignal.stock_id == stock_id, InvestmentSignal.signal_type == sig_type, InvestmentSignal.signal_date < d, InvestmentSignal.signal_date >= d - timedelta(days=days)).limit(1)
    ) is not None


# --- price alerts ---------------------------------------------------------------------------
PRICE_RULES = {"PRICE_ABOVE", "PRICE_BELOW", "ACCUMULATION_ZONE", "RISK_THRESHOLD"}


def _last_two_closes(db: Session, stock_id: int) -> tuple[MarketDataDaily | None, MarketDataDaily | None]:
    rows = db.scalars(select(MarketDataDaily).where(MarketDataDaily.stock_id == stock_id).order_by(MarketDataDaily.trade_date.desc()).limit(2)).all()
    return (rows[0] if rows else None, rows[1] if len(rows) > 1 else None)


def evaluate_price_rule(rule_type: str, cond: dict, prev: float | None, last: float) -> tuple[bool, str, bool]:
    """Returns (triggered, description, is_upward). Crossing detection uses the previous close."""
    if rule_type == "PRICE_ABOVE":
        t = float(cond["price"])
        return (last >= t and (prev is None or prev < t)), f"closed at ${last:,.2f}, crossing above target ${t:,.2f}", True
    if rule_type in ("PRICE_BELOW", "RISK_THRESHOLD"):
        t = float(cond["price"])
        label = "risk threshold" if rule_type == "RISK_THRESHOLD" else "support"
        return (last <= t and (prev is None or prev > t)), f"closed at ${last:,.2f}, falling below {label} ${t:,.2f}", False
    if rule_type == "ACCUMULATION_ZONE":
        lo, hi = float(cond["low"]), float(cond["high"])
        inside = lo <= last <= hi
        was_inside = prev is not None and lo <= prev <= hi
        return (inside and not was_inside), f"closed at ${last:,.2f}, entering accumulation zone ${lo:,.2f}–${hi:,.2f}", False
    raise ValueError(f"unknown rule type {rule_type}")


def price_alerts(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    n = 0
    rules: list[tuple[LineUser, str, str, dict, int, object]] = []
    for r in db.scalars(select(LineAlertRule).where(LineAlertRule.enabled.is_(True), LineAlertRule.rule_type.in_(PRICE_RULES))):
        lu = db.get(LineUser, r.user_id)
        if lu:
            rules.append((lu, r.stock_symbol, r.rule_type, r.conditions_json, r.cooldown_minutes, r))
    for r in db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.rule_type.in_(PRICE_RULES))):
        st = db.get(Stock, r.stock_id) if r.stock_id else None
        for lu in db.scalars(select(LineUser).where(LineUser.user_id == r.user_id)):
            if st:
                rules.append((lu, st.symbol, r.rule_type, r.conditions, r.cooldown_minutes, r))
    for lu, symbol, rtype, cond, cooldown, rule in rules:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol))
        if not stock:
            continue
        last, prev = _last_two_closes(db, stock.id)
        if not last or last.close is None:
            continue
        try:
            hit, desc, up = evaluate_price_rule(rtype, cond, float(prev.close) if prev and prev.close is not None else None, float(last.close))
        except (KeyError, ValueError):
            log.warning("invalid price rule %s", getattr(rule, "id", None))
            continue
        if not hit:
            continue
        msgs = mb.price_alert(stock, rtype.replace("_", " ").title(), [f"{symbol} {desc}.", f"Session {last.trade_date.isoformat()} · {last.data_status} · {last.provider}"], up)
        h = enqueue(db, lu, "PRICE_ALERT", msgs, dedupe_key=f"PRICE:{rtype}:{getattr(rule, 'id', 0)}:{last.trade_date}", title=f"{symbol} {rtype}", stock_symbol=symbol, cooldown_minutes=cooldown, now=now)
        if h:
            rule.last_triggered_at = now
            n += 1
    return n


# --- fundamental alerts ------------------------------------------------------------------------
def filing_alerts(db: Session) -> int:
    n = 0
    filings = db.scalars(select(SecFiling).where(SecFiling.notified.is_(False), SecFiling.form.in_(("10-K", "10-Q", "10-K/A", "10-Q/A")))).all()
    for f in filings:
        stock = db.get(Stock, f.stock_id)
        lines = [f"New {f.form} filed {f.filing_date.isoformat()}" + (f" for period ending {f.report_date.isoformat()}" if f.report_date else "") + "."]
        lines += mb.fundamental_summary_lines(db, stock)[:4]
        msgs = mb.fundamental_alert(stock, f"New {f.form}", lines, f.url)
        for lu in recipients_for(db, "FUNDAMENTAL", stock.symbol):
            if enqueue(db, lu, "FUNDAMENTAL", msgs, dedupe_key=f"FILING:{f.accession_number}", title=f"{stock.symbol} {f.form}", stock_symbol=stock.symbol, cooldown_minutes=0):
                n += 1
        f.notified = True
    return n


def deterioration_alerts(db: Session, stocks: list[Stock]) -> int:
    n = 0
    for stock in stocks:
        flags = fund.detect_deterioration(db, stock.id)
        if not flags:
            continue
        snap = fund.latest_snapshot(db, stock.id)
        key = f"DETERIORATION:{stock.symbol}:{snap.as_of}"
        msgs = mb.fundamental_alert(stock, "Fundamental deterioration", flags)
        for lu in recipients_for(db, "FUNDAMENTAL", stock.symbol):
            if enqueue(db, lu, "FUNDAMENTAL", msgs, dedupe_key=key, title=f"{stock.symbol} deterioration", stock_symbol=stock.symbol, cooldown_minutes=0):
                n += 1
    return n


# --- portfolio alerts ----------------------------------------------------------------------------
def portfolio_alerts(db: Session, big_move_pct: float = 7.0) -> int:
    n = 0
    for lu in db.scalars(select(LineUser).where(LineUser.is_following.is_(True), LineUser.user_id.is_not(None))):
        for p in db.scalars(select(Portfolio).where(Portfolio.user_id == lu.user_id)):
            ev = evaluate_portfolio(db, p)
            lines: list[str] = []
            keys: list[str] = []
            if p.max_drawdown_alert_pct:
                perf = performance_series(db, p, benchmark=None)
                dd = perf.get("metrics", {}).get("current_drawdown_pct")
                if dd is not None and dd <= -float(p.max_drawdown_alert_pct):
                    lines.append(f"Portfolio drawdown {dd:.1f}% exceeds your {float(p.max_drawdown_alert_pct):.0f}% limit.")
                    keys.append(f"dd{int(dd)}")
            for h in ev["holdings"]:
                if h["price_date"] is None:
                    continue
                last, prev = _last_two_closes(db, db.scalar(select(Stock.id).where(Stock.symbol == h["symbol"])))
                if last and prev and prev.close and last.close:
                    chg = (float(last.close) / float(prev.close) - 1) * 100
                    if abs(chg) >= big_move_pct:
                        lines.append(f"{h['symbol']} moved {chg:+.1f}% on {last.trade_date.isoformat()}.")
                        keys.append(f"mv{h['symbol']}{last.trade_date}")
                if p.max_position_pct and h["allocation_pct"] and h["allocation_pct"] > float(p.max_position_pct):
                    lines.append(f"{h['symbol']} is {h['allocation_pct']:.1f}% of the portfolio (limit {float(p.max_position_pct):.0f}%).")
                    keys.append(f"al{h['symbol']}")
                sid = db.scalar(select(Stock.id).where(Stock.symbol == h["symbol"]))
                sig = db.scalar(select(InvestmentSignal).where(InvestmentSignal.stock_id == sid, InvestmentSignal.signal_type == "TREND_WEAKENING").order_by(InvestmentSignal.signal_date.desc()).limit(1))
                if sig and last and sig.signal_date == last.trade_date:
                    lines.append(f"{h['symbol']}: long-term trend deteriorating — review position.")
                    keys.append(f"tw{h['symbol']}{sig.signal_date}")
            if not lines:
                continue
            from app.integrations.line import flex_templates as ft

            card = ft.simple_card(f"💼 {p.name} — review", lines, color=ft.YELLOW, footer_note="Portfolio monitoring only; no trades are executed.")
            msgs = mb.validated([ft.flex(f"Portfolio alert: {p.name}", card)])
            key = f"PORTFOLIO:{p.id}:{date.today().isoformat()}:{'|'.join(sorted(keys))}"[:200]
            if enqueue(db, lu, "PORTFOLIO", msgs, dedupe_key=key, title=f"Portfolio {p.name}", cooldown_minutes=0):
                n += 1
    return n
