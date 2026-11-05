"""Long-Term Investment Score (0-100).

Each component is scored 0-100 from documented sub-metrics using linear
normalization between a "worst" and "best" bound (clipped). A sub-metric with
missing input is excluded from its component (never counted as 0 or as positive).
The total is the weight-averaged score over components that have data, and
`coverage` reports the share of total weight that was actually backed by data.
A score with coverage below 60% is flagged LOW_COVERAGE.

A high score describes current technical/fundamental characteristics. It is not
a forecast and does not guarantee future returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "trend_strength": 30.0,
    "momentum": 20.0,
    "fundamental_quality": 25.0,
    "financial_health": 15.0,
    "relative_performance": 10.0,
}

COMPONENT_LABELS = {
    "trend_strength": "Trend Strength",
    "momentum": "Momentum",
    "fundamental_quality": "Fundamental Quality",
    "financial_health": "Financial Health",
    "relative_performance": "Relative Performance",
}

CLASSIFICATIONS = [
    (80, "STRONG_LONG_TERM_TREND"),
    (65, "BULLISH"),
    (50, "NEUTRAL"),
    (35, "WEAKENING"),
    (0, "BEARISH"),
]
LOW_COVERAGE_THRESHOLD = 0.6


def classify(score: float | None) -> str | None:
    if score is None:
        return None
    for floor, label in CLASSIFICATIONS:
        if score >= floor:
            return label
    return "BEARISH"


def linear(v: float | None, worst: float, best: float) -> float | None:
    """Map v to 0..100 where `worst`->0 and `best`->100 (works for inverted ranges)."""
    if v is None:
        return None
    if best == worst:
        return 100.0 if v >= best else 0.0
    x = (v - worst) / (best - worst)
    return max(0.0, min(1.0, x)) * 100


def rsi_score(rsi: float | None) -> float | None:
    """Long-term momentum view of RSI: healthy 50-65 scores best; overbought and
    oversold score lower (oversold = weak momentum, overbought = stretched)."""
    if rsi is None:
        return None
    if 50 <= rsi <= 65:
        return 100.0
    if 40 <= rsi < 50:
        return 60 + (rsi - 40) * 4  # 60..100
    if 65 < rsi <= 75:
        return 100 - (rsi - 65) * 4  # 100..60
    if rsi > 75:
        return max(30.0, 60 - (rsi - 75) * 3)
    if 30 <= rsi < 40:
        return 25 + (rsi - 30) * 3.5  # 25..60
    return max(0.0, rsi * 25 / 30)


@dataclass
class SubMetric:
    key: str
    label: str
    value: float | None
    score: float | None
    weight: float
    detail: str = ""


@dataclass
class Component:
    key: str
    weight: float
    subs: list[SubMetric] = field(default_factory=list)

    @property
    def available_weight(self) -> float:
        return sum(s.weight for s in self.subs if s.score is not None)

    @property
    def total_sub_weight(self) -> float:
        return sum(s.weight for s in self.subs) or 1.0

    @property
    def coverage(self) -> float:
        return self.available_weight / self.total_sub_weight

    @property
    def score(self) -> float | None:
        aw = self.available_weight
        if aw == 0:
            return None
        return sum(s.score * s.weight for s in self.subs if s.score is not None) / aw

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": COMPONENT_LABELS[self.key],
            "weight": self.weight,
            "score": None if self.score is None else round(self.score, 1),
            "coverage": round(self.coverage, 3),
            "sub_metrics": [
                {"key": s.key, "label": s.label, "value": None if s.value is None else round(s.value, 4), "score": None if s.score is None else round(s.score, 1), "weight": s.weight, "detail": s.detail}
                for s in self.subs
            ],
        }


@dataclass
class ScoreResult:
    total: float | None
    classification: str | None
    coverage: float
    components: dict[str, Component]
    weights: dict[str, float]
    explanation: list[str]
    missing: list[str]
    low_coverage: bool

    def as_dict(self) -> dict:
        return {
            "total": None if self.total is None else round(self.total, 1),
            "classification": self.classification,
            "coverage": round(self.coverage, 3),
            "low_coverage": self.low_coverage,
            "components": {k: c.as_dict() for k, c in self.components.items()},
            "weights": self.weights,
            "explanation": self.explanation,
            "missing": self.missing,
            "disclaimer": "Score reflects current technical and fundamental characteristics; it is not a forecast or a guarantee of future returns.",
        }


def normalize_weights(weights: dict[str, float] | None) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in w and v is not None and v >= 0:
                w[k] = float(v)
    total = sum(w.values()) or 1.0
    return {k: round(v * 100 / total, 4) for k, v in w.items()}


def _bool_score(b: bool | None) -> float | None:
    return None if b is None else (100.0 if b else 0.0)


def compute_score(
    daily: dict,
    weekly: dict | None,
    fundamentals: dict[str, dict] | None,
    relative: dict | None,
    weights: dict[str, float] | None = None,
) -> ScoreResult:
    """daily/weekly: indicator rows. fundamentals: snapshot.values. relative: {'rel_6m':..,'rel_1y':..}."""
    w = normalize_weights(weights)
    d = daily or {}
    wk = weekly or {}
    f = fundamentals or {}
    rel = relative or {}

    def fv(m):
        x = f.get(m)
        return None if x is None else x.get("value")

    c, s50, s200 = d.get("close"), d.get("sma_50"), d.get("sma_200")
    adx_ok = None
    if d.get("adx") is not None and d.get("plus_di") is not None and d.get("minus_di") is not None:
        adx_ok = linear(d["adx"], 10, 35) if d["plus_di"] > d["minus_di"] else 0.0

    trend = Component("trend_strength", w["trend_strength"], [
        SubMetric("price_above_sma200", "Price above SMA 200", d.get("dist_sma_200_pct"), _bool_score(None if c is None or s200 is None else c > s200), 25, f"{_f(d.get('dist_sma_200_pct'))}% vs SMA200"),
        SubMetric("sma50_above_sma200", "SMA 50 above SMA 200", None if s50 is None or s200 is None else s50 / s200 - 1, _bool_score(None if s50 is None or s200 is None else s50 > s200), 20),
        SubMetric("sma200_slope", "SMA 200 slope (20d)", d.get("sma_200_slope_20d_pct"), linear(d.get("sma_200_slope_20d_pct"), -2, 2), 20, f"{_f(d.get('sma_200_slope_20d_pct'))}%"),
        SubMetric("weekly_above_sma40", "Weekly close above SMA 40", wk.get("close"), _bool_score(None if wk.get("close") is None or wk.get("sma_40") is None else wk["close"] > wk["sma_40"]), 20),
        SubMetric("adx_strength", "ADX trend strength (+DI > -DI)", d.get("adx"), adx_ok, 15, f"ADX {_f(d.get('adx'), 1)}"),
    ])
    macd_state = None
    if d.get("macd") is not None and d.get("macd_signal") is not None:
        macd_state = (60.0 if d["macd"] > d["macd_signal"] else 0.0) + (40.0 if d["macd"] > 0 else 0.0)
    momentum = Component("momentum", w["momentum"], [
        SubMetric("rsi", "RSI (14)", d.get("rsi_14"), rsi_score(d.get("rsi_14")), 35, f"RSI {_f(d.get('rsi_14'), 1)}"),
        SubMetric("macd", "MACD above signal / above zero", d.get("macd_hist"), macd_state, 30),
        SubMetric("return_6m", "6-month return", d.get("return_6m"), linear(d.get("return_6m"), -20, 30), 20, f"{_f(d.get('return_6m'))}%"),
        SubMetric("roc_12", "Rate of change (12)", d.get("roc_12"), linear(d.get("roc_12"), -10, 10), 15),
    ])
    quality = Component("fundamental_quality", w["fundamental_quality"], [
        SubMetric("revenue_growth_yoy", "Revenue growth YoY", fv("revenue_growth_yoy"), linear(fv("revenue_growth_yoy"), -10, 25), 25, f"{_f(fv('revenue_growth_yoy'))}%"),
        SubMetric("operating_margin", "Operating margin", fv("operating_margin"), linear(fv("operating_margin"), 0, 35), 20, f"{_f(fv('operating_margin'))}%"),
        SubMetric("net_margin", "Net profit margin", fv("net_margin"), linear(fv("net_margin"), 0, 25), 15),
        SubMetric("roe", "Return on equity", fv("roe"), linear(fv("roe"), 0, 30), 20, f"{_f(fv('roe'))}%"),
        SubMetric("fcf_margin", "Free cash flow margin", fv("fcf_margin"), linear(fv("fcf_margin"), -5, 25), 20),
    ])
    de = fv("debt_to_equity")
    health = Component("financial_health", w["financial_health"], [
        SubMetric("debt_to_equity", "Debt-to-equity", de, linear(de, 2.5, 0.0), 30, f"{_f(de)}"),
        SubMetric("current_ratio", "Current ratio", fv("current_ratio"), linear(fv("current_ratio"), 0.7, 2.0), 25),
        SubMetric("interest_coverage", "Interest coverage", fv("interest_coverage"), linear(fv("interest_coverage"), 1.5, 15), 20),
        SubMetric("fcf_positive", "Positive free cash flow", fv("free_cash_flow"), _bool_score(None if fv("free_cash_flow") is None else fv("free_cash_flow") > 0), 25),
    ])
    relp = Component("relative_performance", w["relative_performance"], [
        SubMetric("rel_6m", f"6-month return vs {rel.get('benchmark', 'SPY')}", rel.get("rel_6m"), linear(rel.get("rel_6m"), -15, 15), 50, f"{_f(rel.get('rel_6m'))} pts"),
        SubMetric("rel_1y", f"1-year return vs {rel.get('benchmark', 'SPY')}", rel.get("rel_1y"), linear(rel.get("rel_1y"), -20, 20), 50, f"{_f(rel.get('rel_1y'))} pts"),
    ])
    comps = {c.key: c for c in (trend, momentum, quality, health, relp)}

    avail = [(c.weight, c.score) for c in comps.values() if c.score is not None]
    total_w = sum(wt for wt, _ in avail)
    total = sum(wt * s for wt, s in avail) / total_w if total_w else None
    coverage = sum(c.weight * c.coverage for c in comps.values()) / 100.0
    missing = [f"{COMPONENT_LABELS[c.key]}: {s.label}" for c in comps.values() for s in c.subs if s.score is None]

    explanation = []
    for comp in comps.values():
        sc = comp.score
        if sc is None:
            explanation.append(f"{COMPONENT_LABELS[comp.key]} (weight {comp.weight:.0f}%): no data — excluded from the total.")
            continue
        good = [s.label for s in comp.subs if s.score is not None and s.score >= 60]
        weak = [s.label for s in comp.subs if s.score is not None and s.score < 40]
        txt = f"{COMPONENT_LABELS[comp.key]} {sc:.0f}/100 (weight {comp.weight:.0f}%, coverage {comp.coverage * 100:.0f}%)"
        if good:
            txt += f"; strengths: {', '.join(good)}"
        if weak:
            txt += f"; weaknesses: {', '.join(weak)}"
        explanation.append(txt + ".")
    low_cov = coverage < LOW_COVERAGE_THRESHOLD
    if low_cov:
        explanation.append(f"Low data coverage ({coverage * 100:.0f}%): treat this score with caution.")
    return ScoreResult(total, classify(total), coverage, comps, w, explanation, missing, low_cov)


def _f(v, nd=2) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"
