from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.analysis.rules import RuleConfig, evaluate
from app.analysis.scoring import classify, compute_score, linear, normalize_weights
from app.analysis.service import analyze_stock
from app.market import calendar
from app.models import DataQualityLog, IndicatorValue, InvestmentScore, InvestmentSignal, MarketDataDaily, MarketDataWeekly, ProviderStatus
from app.pipeline import repository as repo
from app.pipeline.ingestion import InvalidSymbol, update_symbols, validate_symbol_format
from app.providers.base import ProviderUnavailable


def _now_after(d: date) -> datetime:
    return calendar.session_close_utc(d) + timedelta(hours=3)


def test_symbol_validation():
    assert validate_symbol_format("brk.b") == "BRK-B"
    assert validate_symbol_format("^GSPC") == "^GSPC"
    for bad in ("", "AAPL;DROP", "TOOLONGSYMBOL1", "12AB"):
        with pytest.raises(InvalidSymbol):
            validate_symbol_format(bad)


def test_initial_load_and_incremental_update(db, fake_provider):
    end = calendar.latest_completed_session() - timedelta(days=14)
    end = calendar.trading_days(end - timedelta(days=7), end)[-1]
    fake_provider.end = None
    rep = update_symbols(db, ["AAPL", "SPY"], now=_now_after(end), years=2)
    assert {r.status for r in rep.results} == {"UPDATED"}
    aapl = repo.get_stock(db, "AAPL")
    n1 = db.scalar(select(func.count()).select_from(MarketDataDaily).where(MarketDataDaily.stock_id == aapl.id))
    expected = len(calendar.trading_days(end - timedelta(days=int(365.25 * 2)), end))
    assert n1 == expected
    # latest bar END_OF_DAY, older HISTORICAL, provider recorded
    last = db.scalar(select(MarketDataDaily).where(MarketDataDaily.stock_id == aapl.id).order_by(MarketDataDaily.trade_date.desc()).limit(1))
    assert last.trade_date == end and last.data_status == "END_OF_DAY" and last.provider == "fake"
    assert db.scalar(select(func.count()).select_from(MarketDataDaily).where(MarketDataDaily.data_status == "END_OF_DAY", MarketDataDaily.stock_id == aapl.id)) == 1
    assert db.scalar(select(func.count()).select_from(MarketDataWeekly).where(MarketDataWeekly.stock_id == aapl.id)) > 100
    assert db.scalar(select(func.count()).select_from(IndicatorValue).where(IndicatorValue.stock_id == aapl.id, IndicatorValue.timeframe == "DAILY")) == expected

    # same session again -> UP_TO_DATE, no provider calls
    calls = len(fake_provider.calls)
    rep2 = update_symbols(db, ["AAPL"], now=_now_after(end))
    assert rep2.results[0].status == "UP_TO_DATE"
    assert len(fake_provider.calls) == calls

    # 5 sessions later -> incremental fetch with small overlap window only
    later = calendar.trading_days(end + timedelta(days=1), end + timedelta(days=10))[4]
    rep3 = update_symbols(db, ["AAPL"], now=_now_after(later))
    r = rep3.results[0]
    assert r.status == "UPDATED" and not r.full_refresh and r.latest_date == later
    sym, start, _ = fake_provider.calls[-1]
    assert sym == "AAPL" and (end - start).days <= 12
    n2 = db.scalar(select(func.count()).select_from(MarketDataDaily).where(MarketDataDaily.stock_id == aapl.id))
    assert n2 == n1 + 5
    # exactly one END_OF_DAY bar after the update
    assert db.scalar(select(func.count()).select_from(MarketDataDaily).where(MarketDataDaily.data_status == "END_OF_DAY", MarketDataDaily.stock_id == aapl.id)) == 1


def test_upsert_is_idempotent(db, fake_provider):
    end = calendar.latest_completed_session()
    update_symbols(db, ["MSFT"], now=_now_after(end), years=1)
    stock = repo.get_stock(db, "MSFT")
    frame = fake_provider.get_daily_history("MSFT", end - timedelta(days=30), end).frame
    written = repo.upsert_bars(db, "DAILY", stock.id, frame, "fake", end)
    db.commit()
    assert written == 0  # unchanged rows are not rewritten
    total = db.scalar(select(func.count()).select_from(MarketDataDaily).where(MarketDataDaily.stock_id == stock.id))
    assert total == len(calendar.trading_days(end - timedelta(days=int(365.25)), end))


def test_adjustment_change_triggers_full_refresh(db, fake_provider):
    end = calendar.latest_completed_session()
    start_end = calendar.trading_days(end - timedelta(days=20), end - timedelta(days=8))[-1]
    update_symbols(db, ["JNJ"], now=_now_after(start_end), years=1)
    series = fake_provider.series("JNJ").copy()
    series["adj_close"] = series["adj_close"] * 0.98  # dividend re-based history
    fake_provider.overrides["JNJ"] = series
    rep = update_symbols(db, ["JNJ"], now=_now_after(end), years=1)
    assert rep.results[0].full_refresh is True


def test_provider_failure_records_health(db, fake_provider):
    def boom(*a, **k):
        raise ProviderUnavailable("down")

    end = calendar.latest_completed_session()
    update_symbols(db, ["NVDA"], now=_now_after(end), years=1)
    later = end + timedelta(days=10)
    fake_provider.get_daily_history_batch = boom  # type: ignore[assignment]
    fake_provider.get_daily_history = boom  # type: ignore[assignment]
    later_session = calendar.trading_days(end + timedelta(days=1), later)[-1]
    rep = update_symbols(db, ["NVDA"], now=_now_after(later_session))
    assert rep.results[0].status == "FAILED"
    st = db.scalar(select(ProviderStatus).where(ProviderStatus.provider == "fake"))
    assert st.consecutive_failures >= 1 and st.last_error
    assert db.scalar(select(func.count()).select_from(DataQualityLog).where(DataQualityLog.check_type == "PROVIDER_FAILURE")) == 1


def test_unknown_symbol_fails_cleanly(db, fake_provider):
    rep = update_symbols(db, ["ZZZZ"], years=1)
    assert rep.results[0].status in ("FAILED", "NO_DATA")


def test_analysis_generates_signals_and_scores(db, fake_provider):
    fake_provider.params["UPT"] = {"drift": 0.0015, "vol": 0.01}
    end = calendar.latest_completed_session()
    update_symbols(db, ["UPT", "SPY"], now=_now_after(end), years=3)
    stock = repo.get_stock(db, "UPT")
    res = analyze_stock(db, stock, now=_now_after(end))
    db.commit()
    assert res.status == "ANALYZED"
    assert res.score.total is not None and 0 <= res.score.total <= 100
    # fundamentals missing -> coverage < 1 and listed as missing, not zero
    assert res.score.coverage < 0.7
    assert res.score.components["fundamental_quality"].score is None
    sig = db.scalar(select(InvestmentSignal).where(InvestmentSignal.stock_id == stock.id, InvestmentSignal.is_primary.is_(True)))
    assert sig is not None and sig.reasons and sig.provider == "fake" and sig.data_status == "END_OF_DAY"
    assert db.scalar(select(func.count()).select_from(InvestmentScore)) == 1
    # duplicate prevention: second run inserts nothing new
    res2 = analyze_stock(db, stock, now=_now_after(end))
    db.commit()
    assert res2.new_signals == []
    assert db.scalar(select(func.count()).select_from(InvestmentSignal).where(InvestmentSignal.stock_id == stock.id, InvestmentSignal.is_primary.is_(True))) == 1


def test_stale_data_blocks_signals(db, fake_provider):
    end = calendar.latest_completed_session()
    old = calendar.trading_days(end - timedelta(days=30), end - timedelta(days=20))[-1]
    update_symbols(db, ["OLD"], now=_now_after(old), years=2)
    stock = repo.get_stock(db, "OLD")
    res = analyze_stock(db, stock, now=_now_after(end))
    assert res.status == "STALE"
    assert db.scalar(select(func.count()).select_from(InvestmentSignal)) == 0


# --- pure rule / score tests ------------------------------------------------------
BULL_DAILY = {
    "close": 110, "sma_20": 108, "sma_50": 105, "sma_200": 95, "sma_200_slope_20d_pct": 1.2, "adx": 28, "plus_di": 30, "minus_di": 15,
    "rsi_14": 58, "macd": 1.2, "macd_signal": 0.8, "macd_hist": 0.4, "macd_hist_rising": True, "return_6m": 18, "roc_12": 4,
    "dist_sma_50_pct": 4.76, "dist_sma_200_pct": 15.8, "volume_sma_20": 1e6, "volume_sma_50": 1.2e6, "hv_20": 20, "drawdown_from_1y_peak": -3,
}
BULL_WEEKLY = {"close": 110, "sma_40": 98}


def test_bullish_trend_rules():
    ev = evaluate(BULL_DAILY, BULL_WEEKLY)
    assert ev.long_term_trend == "BULLISH" and ev.weekly_trend == "BULLISH"
    assert not ev.risk and not ev.accumulation


def test_bearish_trend_rules():
    d = {**BULL_DAILY, "close": 80, "sma_50": 88, "sma_200": 95, "sma_200_slope_20d_pct": -0.8, "dist_sma_200_pct": -15.8, "macd": -1, "macd_signal": -0.5}
    ev = evaluate(d, {"close": 80, "sma_40": 92})
    assert ev.long_term_trend == "BEARISH"
    assert ev.risk


def test_accumulation_rules():
    d = {**BULL_DAILY, "close": 106, "rsi_14": 45, "dist_sma_50_pct": 0.95, "sma_20": 107}
    ev = evaluate(d, BULL_WEEKLY)
    assert ev.accumulation
    # RSI outside zone -> no accumulation
    assert not evaluate({**d, "rsi_14": 62}, BULL_WEEKLY).accumulation
    # configurable zone
    assert evaluate({**d, "rsi_14": 62}, BULL_WEEKLY, RuleConfig(rsi_accumulation_high=65)).accumulation


def test_overbought_is_flag_not_sell():
    d = {**BULL_DAILY, "rsi_14": 78, "dist_sma_200_pct": 40}
    ev = evaluate(d, BULL_WEEKLY)
    assert ev.overbought and ev.long_term_trend == "BULLISH"


def test_insufficient_data():
    ev = evaluate({"close": 10}, None)
    assert ev.long_term_trend == "INSUFFICIENT_DATA"


def test_score_missing_fundamentals_not_zero():
    s_missing = compute_score(BULL_DAILY, BULL_WEEKLY, {}, {})
    assert s_missing.components["fundamental_quality"].score is None
    assert s_missing.coverage == pytest.approx(0.30 + 0.20, abs=0.01)
    assert s_missing.total > 70  # not dragged to zero by missing fundamentals
    assert any("Fundamental Quality" in m for m in s_missing.missing)
    weak = {"revenue_growth_yoy": {"value": -15}, "operating_margin": {"value": -5}, "net_margin": {"value": -8}, "roe": {"value": -10}, "fcf_margin": {"value": -10}}
    s_weak = compute_score(BULL_DAILY, BULL_WEEKLY, weak, {})
    assert s_weak.components["fundamental_quality"].score == 0
    assert s_weak.total < s_missing.total


def test_score_weights_and_classification():
    w = normalize_weights({"trend_strength": 60, "momentum": 0, "fundamental_quality": 0, "financial_health": 0, "relative_performance": 0})
    assert w["trend_strength"] == 100
    assert classify(85) == "STRONG_LONG_TERM_TREND" and classify(70) == "BULLISH" and classify(55) == "NEUTRAL"
    assert classify(40) == "WEAKENING" and classify(10) == "BEARISH"
    assert linear(5, 0, 10) == 50 and linear(-1, 0, 10) == 0 and linear(0.5, 2, 0) == 75
