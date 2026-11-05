"""Analysis orchestration: freshness gate -> rules -> score -> signals (persisted)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.analysis import fundamentals as fund
from app.analysis.rules import RuleConfig, RuleEvaluation, evaluate
from app.analysis.scoring import ScoreResult, compute_score
from app.core.config import get_settings
from app.models import IndicatorValue, InvestmentScore, InvestmentSignal, MarketDataDaily, Stock
from app.pipeline import repository as repo
from app.pipeline.quality import freshness

log = logging.getLogger(__name__)

BULLISH_TYPES = {"STRONG_LONG_TERM_TREND", "BULLISH", "ACCUMULATION_WATCH"}
BEARISH_TYPES = {"WEAKENING", "BEARISH", "TREND_WEAKENING"}


@dataclass
class AnalysisResult:
    symbol: str
    stock_id: int
    status: str  # ANALYZED | STALE | UNAVAILABLE | INSUFFICIENT_DATA
    trade_date: date | None = None
    freshness: dict | None = None
    rules: RuleEvaluation | None = None
    score: ScoreResult | None = None
    relative: dict | None = None
    primary_signal: str | None = None
    previous_primary_signal: str | None = None
    flags: list[str] = field(default_factory=list)
    provider: str | None = None
    data_status: str | None = None
    daily: dict | None = None
    weekly: dict | None = None
    new_signals: list[str] = field(default_factory=list)

    @property
    def signal_changed(self) -> bool:
        return self.primary_signal is not None and self.primary_signal != self.previous_primary_signal


def _latest_complete_weekly(db: Session, stock_id: int, on_or_before: date) -> dict | None:
    rows = db.scalars(
        select(IndicatorValue)
        .where(IndicatorValue.stock_id == stock_id, IndicatorValue.timeframe == "WEEKLY", IndicatorValue.trade_date <= on_or_before)
        .order_by(IndicatorValue.trade_date.desc())
        .limit(2)
    ).all()
    for r in rows:
        if r.values.get("is_complete", True):
            return {**r.values, "trade_date": r.trade_date.isoformat()}
    return None


def relative_performance(db: Session, stock_id: int, on: date, benchmark: str = "SPY") -> dict:
    out: dict = {"benchmark": benchmark}
    bench = repo.get_stock(db, benchmark)
    if not bench or bench.id == stock_id:
        return out
    s = repo.latest_indicators(db, stock_id, "DAILY", on)
    b = db.scalar(select(IndicatorValue).where(IndicatorValue.stock_id == bench.id, IndicatorValue.timeframe == "DAILY", IndicatorValue.trade_date == (s.trade_date if s else on)))
    if not s or not b:
        return out
    for k in ("1m", "3m", "6m", "1y"):
        sv, bv = s.values.get(f"return_{k}"), b.values.get(f"return_{k}")
        out[f"rel_{k}"] = None if sv is None or bv is None else round(sv - bv, 4)
        out[f"stock_{k}"] = sv
        out[f"benchmark_{k}"] = bv
    known = [out.get(f"rel_{k}") for k in ("3m", "6m", "1y") if out.get(f"rel_{k}") is not None]
    out["outperforming"] = None if not known else sum(1 for x in known if x > 0) >= 2
    return out


def relative_strength_series(db: Session, stock_id: int, benchmark_id: int, start: date | None = None) -> list[dict]:
    """RS line = stock adj close / benchmark adj close, rebased to 100."""
    s = repo.load_bars(db, "DAILY", stock_id, start=start)
    b = repo.load_bars(db, "DAILY", benchmark_id, start=start)
    if s.empty or b.empty:
        return []
    sa = s["adj_close"].fillna(s["close"])
    ba = b["adj_close"].fillna(b["close"])
    joined = sa.to_frame("s").join(ba.to_frame("b"), how="inner").dropna()
    if joined.empty:
        return []
    rs = joined["s"] / joined["b"]
    rs = rs / rs.iloc[0] * 100
    return [{"date": d.isoformat(), "rs": round(float(v), 4)} for d, v in rs.items()]


def _confirmation(signal_type: str, weekly_trend: str | None) -> str:
    if signal_type in BULLISH_TYPES:
        return "PENDING" if weekly_trend is None else ("CONFIRMED" if weekly_trend == "BULLISH" else "UNCONFIRMED")
    if signal_type in BEARISH_TYPES:
        return "PENDING" if weekly_trend is None else ("CONFIRMED" if weekly_trend == "BEARISH" else "UNCONFIRMED")
    return "NOT_APPLICABLE"


def _reasons(signal_type: str, ev: RuleEvaluation, score: ScoreResult | None) -> list[str]:
    if signal_type == "ACCUMULATION_WATCH":
        conds = ev.accumulation_conditions
    elif signal_type == "OVERBOUGHT":
        conds = ev.overbought_conditions
    elif signal_type == "TREND_WEAKENING":
        conds = ev.risk_conditions
    elif signal_type in ("WEAKENING", "BEARISH"):
        conds = ev.bearish_conditions + ev.risk_conditions
    else:
        conds = ev.trend_conditions
    out = [f"{c.label} ({c.detail})" for c in conds if c.passed]
    if score and score.total is not None and signal_type in ("STRONG_LONG_TERM_TREND", "BULLISH", "NEUTRAL", "WEAKENING", "BEARISH"):
        out.insert(0, f"Investment score {score.total:.0f}/100 → {score.classification} (coverage {score.coverage * 100:.0f}%)")
    return out


def analyze_stock(
    db: Session,
    stock: Stock,
    *,
    now: datetime | None = None,
    cfg: RuleConfig | None = None,
    weights: dict | None = None,
    persist: bool = True,
    benchmark: str = "SPY",
) -> AnalysisResult:
    latest_bar = repo.latest_bar_date(db, stock.id)
    fr = freshness(latest_bar, now)
    res = AnalysisResult(symbol=stock.symbol, stock_id=stock.id, status="ANALYZED", trade_date=latest_bar, freshness=fr)
    if fr["status"] == "UNAVAILABLE":
        res.status = "UNAVAILABLE"
        return res
    if fr["status"] == "STALE":
        # Never generate new signals from stale data.
        res.status = "STALE"
        return res
    bar = db.scalar(select(MarketDataDaily).where(MarketDataDaily.stock_id == stock.id, MarketDataDaily.trade_date == latest_bar))
    ind = repo.latest_indicators(db, stock.id, "DAILY", latest_bar)
    if not ind or ind.trade_date != latest_bar:
        res.status = "INSUFFICIENT_DATA"
        return res
    daily = ind.values
    weekly = _latest_complete_weekly(db, stock.id, latest_bar)
    snap = fund.latest_snapshot(db, stock.id)
    flags = fund.detect_deterioration(db, stock.id)
    rel = relative_performance(db, stock.id, latest_bar, benchmark)
    ev = evaluate(daily, weekly, cfg, flags)
    score = compute_score(daily, weekly, snap.values, rel, weights)
    res.rules, res.score, res.relative, res.daily, res.weekly = ev, score, rel, daily, weekly
    res.provider, res.data_status = bar.provider, bar.data_status
    if ev.long_term_trend == "INSUFFICIENT_DATA" or score.total is None:
        res.status = "INSUFFICIENT_DATA"
        return res

    primary = "ACCUMULATION_WATCH" if ev.accumulation else score.classification
    flags_out = []
    if ev.risk:
        flags_out.append("TREND_WEAKENING")
    if ev.overbought:
        flags_out.append("OVERBOUGHT")
    res.primary_signal, res.flags = primary, flags_out

    prev = db.scalar(
        select(InvestmentSignal.signal_type)
        .where(InvestmentSignal.stock_id == stock.id, InvestmentSignal.timeframe == "DAILY", InvestmentSignal.is_primary.is_(True), InvestmentSignal.signal_date < latest_bar)
        .order_by(InvestmentSignal.signal_date.desc())
        .limit(1)
    )
    res.previous_primary_signal = prev
    if persist:
        res.new_signals = _persist(db, stock, latest_bar, res, ev, score)
    return res


def _persist(db: Session, stock: Stock, d: date, res: AnalysisResult, ev: RuleEvaluation, score: ScoreResult) -> list[str]:
    snapshot_keys = (
        "close", "sma_20", "sma_50", "sma_200", "ema_50", "ema_200", "rsi_14", "macd", "macd_signal", "macd_hist", "adx",
        "plus_di", "minus_di", "atr_14", "bb_upper", "bb_lower", "dist_sma_50_pct", "dist_sma_200_pct", "return_1y",
        "high_52w", "low_52w", "volume", "volume_sma_20", "volume_sma_50", "drawdown_from_1y_peak", "hv_20",
    )
    snap = {k: res.daily.get(k) for k in snapshot_keys}
    if res.weekly:
        snap["weekly_close"] = res.weekly.get("close")
        snap["weekly_sma_40"] = res.weekly.get("sma_40")
        snap["weekly_trade_date"] = res.weekly.get("trade_date")
    snap["long_term_trend"] = ev.long_term_trend
    snap["weekly_trend"] = ev.weekly_trend

    table = InvestmentSignal.__table__
    types = [(res.primary_signal, True)] + [(f, False) for f in res.flags] + [(e, False) for e in ev.events]
    inserted: list[str] = []
    for sig_type, is_primary in types:
        conds_passed_ratio = None
        if sig_type == "ACCUMULATION_WATCH":
            c = ev.accumulation_conditions
            conds_passed_ratio = sum(1 for x in c if x.passed) / len(c)
        confidence = round(score.coverage * (conds_passed_ratio if conds_passed_ratio is not None else 1.0), 3)
        stmt = insert(table).values(
            stock_id=stock.id,
            signal_date=d,
            timeframe="DAILY",
            signal_type=sig_type,
            is_primary=is_primary,
            score=round(score.total, 1) if score.total is not None else None,
            confidence=confidence,
            confirmation_status=_confirmation(sig_type, ev.weekly_trend),
            indicator_snapshot=snap,
            reasons=_reasons(sig_type, ev, score),
            provider=res.provider,
            data_status=res.data_status,
            created_at=datetime.now(UTC),
        ).on_conflict_do_nothing(constraint="uq_signal_stock_tf_date_type").returning(table.c.id)
        if db.execute(stmt).first() is not None:
            inserted.append(sig_type)

    sc = insert(InvestmentScore.__table__).values(
        stock_id=stock.id,
        score_date=d,
        total_score=None if score.total is None else round(score.total, 2),
        classification=score.classification,
        coverage=round(score.coverage, 4),
        components={k: c.as_dict() for k, c in score.components.items()},
        weights=score.weights,
        explanation=score.explanation,
        missing=score.missing,
        created_at=datetime.now(UTC),
    )
    sc = sc.on_conflict_do_update(
        constraint="uq_score_stock_date",
        set_={k: sc.excluded[k] for k in ("total_score", "classification", "coverage", "components", "weights", "explanation", "missing", "created_at")},
    )
    db.execute(sc)
    return inserted


def analyze_stocks(db: Session, stocks: list[Stock], **kw) -> list[AnalysisResult]:
    out = []
    for s in stocks:
        try:
            out.append(analyze_stock(db, s, **kw))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("analysis failed for %s", s.symbol)
    return out


def default_benchmark() -> str:
    lst = get_settings().benchmark_list
    return lst[0] if lst else "SPY"
