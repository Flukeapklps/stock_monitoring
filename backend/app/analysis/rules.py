"""Configurable rule-based signal engine for long-term investing.

The engine is a pure function of indicator snapshots (daily + weekly) so it can
be unit tested and reused by the screener and the backtester. Every condition
records its inputs, so each signal is fully explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class RuleConfig(BaseModel):
    # trend
    adx_trend_min: float = Field(20.0, description="ADX above this indicates meaningful trend strength")
    # accumulation
    rsi_accumulation_low: float = 40.0
    rsi_accumulation_high: float = 50.0
    support_proximity_pct: float = Field(3.0, description="Price within this % above SMA 50/200 counts as 'approaching support'")
    support_below_tolerance_pct: float = Field(1.5, description="Allow price slightly below the MA while still 'at support'")
    # overbought
    rsi_overbought: float = 70.0
    extension_above_sma200_pct: float = 25.0
    short_term_extension_pct: float = Field(10.0, description="Price this % above SMA 20 = extreme short-term extension")
    high_volatility_hv20: float = Field(60.0, description="20d historical volatility (annualized %) considered abnormally high")
    # risk
    large_drawdown_pct: float = Field(20.0, description="Drawdown from 1y peak considered large")


@dataclass
class Condition:
    key: str
    label: str
    passed: bool | None  # None = insufficient data
    detail: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "passed": self.passed, "detail": self.detail}


@dataclass
class RuleEvaluation:
    long_term_trend: str  # BULLISH | BEARISH | NEUTRAL | INSUFFICIENT_DATA
    trend_conditions: list[Condition] = field(default_factory=list)
    bearish_conditions: list[Condition] = field(default_factory=list)
    accumulation: bool = False
    accumulation_conditions: list[Condition] = field(default_factory=list)
    overbought: bool = False
    overbought_conditions: list[Condition] = field(default_factory=list)
    risk: bool = False
    risk_conditions: list[Condition] = field(default_factory=list)
    weekly_trend: str | None = None  # BULLISH | BEARISH | None
    events: list[str] = field(default_factory=list)  # GOLDEN_CROSS, DEATH_CROSS, MACD_BULL_CROSS ...

    def as_dict(self) -> dict:
        return {
            "long_term_trend": self.long_term_trend,
            "weekly_trend": self.weekly_trend,
            "trend_conditions": [c.as_dict() for c in self.trend_conditions],
            "bearish_conditions": [c.as_dict() for c in self.bearish_conditions],
            "accumulation": self.accumulation,
            "accumulation_conditions": [c.as_dict() for c in self.accumulation_conditions],
            "overbought": self.overbought,
            "overbought_conditions": [c.as_dict() for c in self.overbought_conditions],
            "risk": self.risk,
            "risk_conditions": [c.as_dict() for c in self.risk_conditions],
            "events": self.events,
        }


def _g(d: dict | None, k: str):
    if not d:
        return None
    v = d.get(k)
    return None if v is None else v


def _cmp(a, b, op) -> bool | None:
    if a is None or b is None:
        return None
    return op(a, b)


def _fmt(v, nd=2) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"


def evaluate(daily: dict, weekly: dict | None, cfg: RuleConfig | None = None, fundamental_flags: list[str] | None = None) -> RuleEvaluation:
    """Evaluate rules on the latest daily indicator row and latest *complete* weekly row."""
    cfg = cfg or RuleConfig()
    c = _g(daily, "close")
    s20, s50, s200 = _g(daily, "sma_20"), _g(daily, "sma_50"), _g(daily, "sma_200")
    slope200 = _g(daily, "sma_200_slope_20d_pct")
    adx, pdi, mdi = _g(daily, "adx"), _g(daily, "plus_di"), _g(daily, "minus_di")
    rsi = _g(daily, "rsi_14")
    hist, hist_rising = _g(daily, "macd_hist"), _g(daily, "macd_hist_rising")
    macd_line, macd_sig = _g(daily, "macd"), _g(daily, "macd_signal")
    wc, w40 = _g(weekly, "close"), _g(weekly, "sma_40")

    gt = lambda a, b: a > b  # noqa: E731
    lt = lambda a, b: a < b  # noqa: E731

    weekly_trend = None
    if wc is not None and w40 is not None:
        weekly_trend = "BULLISH" if wc > w40 else "BEARISH"

    trend = [
        Condition("price_above_sma200", "Price above SMA 200", _cmp(c, s200, gt), f"close {_fmt(c)} vs SMA200 {_fmt(s200)}"),
        Condition("sma50_above_sma200", "SMA 50 above SMA 200", _cmp(s50, s200, gt), f"SMA50 {_fmt(s50)} vs SMA200 {_fmt(s200)}"),
        Condition("sma200_rising", "SMA 200 trending upward", _cmp(slope200, 0, gt), f"20-day slope {_fmt(slope200)}%"),
        Condition("weekly_above_sma40", "Weekly close above weekly SMA 40", _cmp(wc, w40, gt), f"weekly close {_fmt(wc)} vs SMA40 {_fmt(w40)}"),
        Condition(
            "adx_trend_strength",
            "ADX indicates meaningful trend strength",
            None if adx is None or pdi is None or mdi is None else (adx >= cfg.adx_trend_min and pdi > mdi),
            f"ADX {_fmt(adx, 1)} (+DI {_fmt(pdi, 1)} / -DI {_fmt(mdi, 1)})",
        ),
    ]
    bearish = [
        Condition("price_below_sma200", "Price below SMA 200", _cmp(c, s200, lt), f"close {_fmt(c)} vs SMA200 {_fmt(s200)}"),
        Condition("sma50_below_sma200", "SMA 50 below SMA 200", _cmp(s50, s200, lt), f"SMA50 {_fmt(s50)} vs SMA200 {_fmt(s200)}"),
        Condition("sma200_falling", "SMA 200 trending downward", _cmp(slope200, 0, lt), f"20-day slope {_fmt(slope200)}%"),
        Condition("weekly_below_sma40", "Weekly close below weekly SMA 40", _cmp(wc, w40, lt), f"weekly close {_fmt(wc)} vs SMA40 {_fmt(w40)}"),
    ]
    core_known = [x.passed for x in trend[:3] if x.passed is not None]
    bull_count = sum(1 for x in trend if x.passed)
    bear_count = sum(1 for x in bearish if x.passed)
    if len(core_known) < 2:
        lt_trend = "INSUFFICIENT_DATA"
    elif trend[0].passed and trend[1].passed and bull_count >= 3:
        lt_trend = "BULLISH"
    elif bearish[0].passed and bear_count >= 3:
        lt_trend = "BEARISH"
    else:
        lt_trend = "NEUTRAL"

    # --- accumulation ---------------------------------------------------------
    def near(ma):
        if c is None or ma is None:
            return None
        d = (c / ma - 1) * 100
        return -cfg.support_below_tolerance_pct <= d <= cfg.support_proximity_pct

    near50, near200 = near(s50), near(s200)
    near_support = None if near50 is None and near200 is None else bool(near50 or near200)
    rsi_zone = None if rsi is None else cfg.rsi_accumulation_low <= rsi <= cfg.rsi_accumulation_high
    macd_improving = None if hist is None else bool(hist_rising) or bool(_g(daily, "macd_bull_cross"))
    vol20, vol50 = _g(daily, "volume_sma_20"), _g(daily, "volume_sma_50")
    vol_declining = _cmp(vol20, vol50, lt)
    low52 = _g(daily, "low_52w")
    stabilizing = None
    if c is not None and s20 is not None:
        stabilizing = c >= s20 * 0.98  # holding near/above short-term average
    accumulation = [
        Condition("uptrend", "Established long-term uptrend", lt_trend == "BULLISH", f"long-term trend {lt_trend}"),
        Condition("rsi_zone", f"RSI in {cfg.rsi_accumulation_low:.0f}-{cfg.rsi_accumulation_high:.0f} accumulation range", rsi_zone, f"RSI(14) {_fmt(rsi, 1)}"),
        Condition("near_support", "Price approaches SMA 50 or SMA 200 support", near_support, f"{_fmt(_g(daily, 'dist_sma_50_pct'))}% from SMA50, {_fmt(_g(daily, 'dist_sma_200_pct'))}% from SMA200"),
        Condition("macd_improving", "MACD momentum improving", macd_improving, f"MACD hist {_fmt(hist, 3)} ({'rising' if hist_rising else 'falling'})"),
        Condition("volume_declining", "Selling volume declining", vol_declining, f"Vol SMA20 {_fmt(vol20, 0)} vs SMA50 {_fmt(vol50, 0)}"),
        Condition("stabilizing", "Price stabilizing near support", stabilizing, f"close vs SMA20 {_fmt(s20)}; 52w low {_fmt(low52)}"),
    ]
    required_ok = all(x.passed for x in accumulation[:3])
    supporting = sum(1 for x in accumulation[3:] if x.passed)
    is_accum = bool(required_ok and supporting >= 1)

    # --- overbought ------------------------------------------------------------
    dist200 = _g(daily, "dist_sma_200_pct")
    ext20 = None if c is None or s20 is None else (c / s20 - 1) * 100
    hv = _g(daily, "hv_20")
    ob = [
        Condition("rsi_overbought", f"RSI above {cfg.rsi_overbought:.0f}", _cmp(rsi, cfg.rsi_overbought, gt), f"RSI(14) {_fmt(rsi, 1)}"),
        Condition("extended_sma200", f"Price > {cfg.extension_above_sma200_pct:.0f}% above SMA 200", _cmp(dist200, cfg.extension_above_sma200_pct, gt), f"{_fmt(dist200)}% above SMA200"),
        Condition("short_term_extension", "Extreme short-term extension", _cmp(ext20, cfg.short_term_extension_pct, gt), f"{_fmt(ext20)}% above SMA20"),
        Condition("high_volatility", "Abnormally high volatility", _cmp(hv, cfg.high_volatility_hv20, gt), f"HV20 {_fmt(hv, 1)}%"),
    ]
    is_ob = bool(ob[0].passed) and sum(1 for x in ob if x.passed) >= 2 or sum(1 for x in ob[1:] if x.passed) >= 2

    # --- risk ------------------------------------------------------------------
    dd = _g(daily, "drawdown_from_1y_peak")
    risk = [
        Condition("weekly_below_sma40", "Weekly close below SMA 40", _cmp(wc, w40, lt), f"weekly close {_fmt(wc)} vs SMA40 {_fmt(w40)}"),
        Condition("daily_below_sma200", "Daily close below SMA 200", _cmp(c, s200, lt), f"close {_fmt(c)} vs SMA200 {_fmt(s200)}"),
        Condition("death_cross", "Death cross (SMA 50 below SMA 200)", _cmp(s50, s200, lt), "SMA50 crossed below SMA200" if _g(daily, "death_cross") else f"SMA50 {_fmt(s50)} / SMA200 {_fmt(s200)}"),
        Condition("sma200_declining", "Declining 200-day moving average", _cmp(slope200, 0, lt), f"20-day slope {_fmt(slope200)}%"),
        Condition("large_drawdown", f"Drawdown over {cfg.large_drawdown_pct:.0f}% from 1y peak", _cmp(dd, -cfg.large_drawdown_pct, lt), f"drawdown {_fmt(dd)}%"),
        Condition("macd_negative", "MACD indicates negative momentum", None if macd_line is None or macd_sig is None else (macd_line < macd_sig and macd_line < 0), f"MACD {_fmt(macd_line, 3)} vs signal {_fmt(macd_sig, 3)}"),
    ]
    if fundamental_flags:
        risk.append(Condition("fundamental_deterioration", "Negative earnings / financial deterioration", True, "; ".join(fundamental_flags)))
    risk_count = sum(1 for x in risk if x.passed)
    is_risk = bool(risk[1].passed and (risk[0].passed or risk[3].passed)) or risk_count >= 3

    events = []
    for k, label in (("golden_cross", "GOLDEN_CROSS"), ("death_cross", "DEATH_CROSS"), ("macd_bull_cross", "MACD_BULL_CROSS"), ("macd_bear_cross", "MACD_BEAR_CROSS")):
        if _g(daily, k):
            events.append(label)

    return RuleEvaluation(
        long_term_trend=lt_trend,
        trend_conditions=trend,
        bearish_conditions=bearish,
        accumulation=is_accum,
        accumulation_conditions=accumulation,
        overbought=is_ob,
        overbought_conditions=ob,
        risk=is_risk,
        risk_conditions=risk,
        weekly_trend=weekly_trend,
        events=events,
    )
