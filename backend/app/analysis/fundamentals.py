"""Fundamental analysis from SEC EDGAR XBRL company facts.

Period handling rules (to avoid mixing incompatible accounting periods):
  * FY values: duration facts of ~1 year (330-400 days) from annual forms (10-K, 20-F, 40-F).
    Keyed by period END date; when several filings report the same period, the
    most recently filed value wins (captures restatements). Comparatives from
    later 10-Ks are therefore used for prior years.
  * TTM values: latest FY + current year-to-date - prior-year same YTD (10-Q),
    only when both YTD facts exist with matching durations; otherwise TTM = latest FY
    when the FY is the most recent period.
  * Instant (balance sheet) values: latest reported period end from 10-K/10-Q.
  * Growth compares FY to the FY ending ~1 year earlier (and TTM to TTM one year earlier).
Every stored metric records period_end, fiscal year/period, form, filed date and
whether it was reported or calculated. Missing inputs produce *missing* metrics,
never zeros.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FundamentalData, SecFiling, Stock
from app.providers import registry
from app.providers.base import ProviderError

log = logging.getLogger(__name__)

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTER_FORMS = {"10-Q", "10-Q/A"}

DURATION_TAGS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "depreciation_amortization": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"],
}
INSTANT_TAGS: dict[str, list[str]] = {
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "long_term_debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "short_term_borrowings": ["ShortTermBorrowings", "CommercialPaper"],
}


@dataclass
class Fact:
    metric: str
    value: float
    start: date | None
    end: date
    fy: int | None
    fp: str | None
    form: str
    filed: date
    accn: str
    tag: str
    unit: str

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days if self.start else None


@dataclass
class FundamentalSnapshot:
    as_of: date | None
    values: dict[str, dict] = field(default_factory=dict)  # metric -> {value, period_end, period_type, is_calculated, ...}
    missing: list[str] = field(default_factory=list)


def _parse_facts(facts: dict, tag: str, metric: str) -> list[Fact]:
    node = (facts.get("facts", {}).get("us-gaap", {}) or {}).get(tag) or (facts.get("facts", {}).get("ifrs-full", {}) or {}).get(tag)
    if not node:
        return []
    out: list[Fact] = []
    for unit, items in node.get("units", {}).items():
        if unit not in ("USD", "USD/shares"):
            continue
        for it in items:
            try:
                out.append(
                    Fact(
                        metric=metric,
                        value=float(it["val"]),
                        start=date.fromisoformat(it["start"]) if it.get("start") else None,
                        end=date.fromisoformat(it["end"]),
                        fy=it.get("fy"),
                        fp=it.get("fp"),
                        form=it.get("form", ""),
                        filed=date.fromisoformat(it["filed"]),
                        accn=it.get("accn", ""),
                        tag=tag,
                        unit=unit,
                    )
                )
            except (KeyError, ValueError):
                continue
    return out


def _latest_by_period(facts: list[Fact]) -> dict[tuple[date | None, date], Fact]:
    best: dict[tuple[date | None, date], Fact] = {}
    for f in facts:
        k = (f.start, f.end)
        if k not in best or f.filed > best[k].filed:
            best[k] = f
    return best


def _annual(facts: list[Fact]) -> list[Fact]:
    sel = [f for f in facts if f.form in ANNUAL_FORMS and f.days is not None and 330 <= f.days <= 400]
    by_end: dict[date, Fact] = {}
    for f in _latest_by_period(sel).values():
        if f.end not in by_end or f.filed > by_end[f.end].filed:
            by_end[f.end] = f
    return sorted(by_end.values(), key=lambda f: f.end)


def _ytd(facts: list[Fact]) -> list[Fact]:
    """Year-to-date duration facts from 10-Qs (3, 6 or 9 months)."""
    sel = [f for f in facts if f.form in QUARTER_FORMS and f.days is not None and 80 <= f.days <= 290]
    return sorted(_latest_by_period(sel).values(), key=lambda f: (f.end, f.days or 0))


def _pick_tag(facts_json: dict, tags: list[str], metric: str) -> list[Fact]:
    """Use the first tag that yields data for the most recent period (companies switch tags over time)."""
    candidates = [(t, _parse_facts(facts_json, t, metric)) for t in tags]
    candidates = [(t, fs) for t, fs in candidates if fs]
    if not candidates:
        return []
    # prefer the tag with the most recent period end; merge older tags for history
    candidates.sort(key=lambda tf: max(f.end for f in tf[1]), reverse=True)
    primary = candidates[0][1]
    merged = list(primary)
    covered = {(f.start, f.end) for f in primary}
    for _t, fs in candidates[1:]:
        merged += [f for f in fs if (f.start, f.end) not in covered]
    return merged


def compute_ttm(facts: list[Fact]) -> tuple[float, date, str] | None:
    annual = _annual(facts)
    if not annual:
        return None
    fy = annual[-1]
    ytds = [f for f in _ytd(facts) if f.end > fy.end]
    if not ytds:
        return fy.value, fy.end, "FY"
    cur = max(ytds, key=lambda f: (f.end, f.days or 0))
    # prior-year YTD of the same length ending ~1 year earlier
    prior = [
        f
        for f in _ytd(facts)
        if f.days is not None and cur.days is not None and abs(f.days - cur.days) <= 10 and 350 <= (cur.end - f.end).days <= 380
    ]
    if not prior:
        return fy.value, fy.end, "FY"
    p = max(prior, key=lambda f: f.filed)
    return fy.value + cur.value - p.value, cur.end, "TTM"


def extract_fundamentals(facts_json: dict) -> dict:
    """Return {'annual': {metric: [Fact,...]}, 'ttm': {metric: (value, end, basis)}, 'instant': {metric: Fact}}."""
    annual: dict[str, list[Fact]] = {}
    ttm: dict[str, tuple[float, date, str]] = {}
    instant: dict[str, Fact] = {}
    for metric, tags in DURATION_TAGS.items():
        fs = _pick_tag(facts_json, tags, metric)
        if not fs:
            continue
        a = _annual(fs)
        if a:
            annual[metric] = a
        t = compute_ttm(fs)
        if t:
            ttm[metric] = t
    for metric, tags in INSTANT_TAGS.items():
        fs = [f for f in _pick_tag(facts_json, tags, metric) if f.start is None and (f.form in ANNUAL_FORMS | QUARTER_FORMS)]
        if fs:
            instant[metric] = max(fs, key=lambda f: (f.end, f.filed))
    shares = (facts_json.get("facts", {}).get("dei", {}) or {}).get("EntityCommonStockSharesOutstanding")
    if shares:
        items = [i for i in shares.get("units", {}).get("shares", []) if i.get("val")]
        if items:
            latest = max(items, key=lambda i: (i["end"], i["filed"]))
            instant["shares_outstanding"] = Fact(
                "shares_outstanding", float(latest["val"]), None, date.fromisoformat(latest["end"]), latest.get("fy"), latest.get("fp"),
                latest.get("form", ""), date.fromisoformat(latest["filed"]), latest.get("accn", ""), "dei:EntityCommonStockSharesOutstanding", "shares",
            )
    return {"annual": annual, "ttm": ttm, "instant": instant}


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def derive_metrics(ext: dict) -> list[dict]:
    """Produce metric rows (reported + calculated) ready for storage."""
    rows: list[dict] = []
    annual, ttm, instant = ext["annual"], ext["ttm"], ext["instant"]

    def add(metric, value, period_type, end, *, start=None, fy=None, fp=None, form=None, filed=None, accn=None, calc=False, tag=None, unit="USD"):
        if value is None:
            return
        rows.append(dict(metric=metric, value=float(value), period_type=period_type, period_end=end, period_start=start, fiscal_year=fy,
                         fiscal_period=fp, form=form, filed_date=filed, accession_number=accn, is_calculated=calc, source_tag=tag, unit=unit))

    # reported annual history
    for metric, facts in annual.items():
        for f in facts[-6:]:
            add(metric, f.value, "FY", f.end, start=f.start, fy=f.fy, fp="FY", form=f.form, filed=f.filed, accn=f.accn, tag=f"us-gaap:{f.tag}", unit=f.unit)
    for metric, (val, end, basis) in ttm.items():
        add(metric, val, "TTM", end, calc=(basis == "TTM"), tag=f"derived:{basis}", unit="USD/shares" if metric == "eps_diluted" else "USD")
    for metric, f in instant.items():
        add(metric, f.value, "INSTANT", f.end, fy=f.fy, fp=f.fp, form=f.form, filed=f.filed, accn=f.accn, tag=f.tag if ":" in f.tag else f"us-gaap:{f.tag}", unit=f.unit)

    # calculated annual metrics per fiscal year
    def series(metric):
        return {f.end: f for f in annual.get(metric, [])}

    rev, gp, cogs, op, ni, ocf, capex = (series(m) for m in ("revenue", "gross_profit", "cost_of_revenue", "operating_income", "net_income", "operating_cash_flow", "capex"))
    eps = series("eps_diluted")
    for end, r in rev.items():
        g = gp[end].value if end in gp else (r.value - cogs[end].value if end in cogs else None)
        add("gross_margin", _pct(_safe_div(g, r.value)), "FY", end, fy=r.fy, calc=True, unit="%")
        if end in op:
            add("operating_margin", _pct(_safe_div(op[end].value, r.value)), "FY", end, fy=r.fy, calc=True, unit="%")
        if end in ni:
            add("net_margin", _pct(_safe_div(ni[end].value, r.value)), "FY", end, fy=r.fy, calc=True, unit="%")
        if end in ocf:
            # FCF requires both OCF and capex for the same period; otherwise it is missing
            fcf = ocf[end].value - capex[end].value if end in capex else None
            add("free_cash_flow", fcf, "FY", end, fy=r.fy, calc=True)
            add("fcf_margin", _pct(_safe_div(fcf, r.value)), "FY", end, fy=r.fy, calc=True, unit="%")
    for name, ser in (("revenue_growth_yoy", rev), ("eps_growth_yoy", eps), ("net_income_growth_yoy", ni)):
        _growth(add, name, ser)
    fcf_series = {}
    for end in ocf:
        if end in capex:
            fcf_series[end] = ocf[end].value - capex[end].value
    _growth_values(add, "fcf_growth_yoy", fcf_series)

    # balance-sheet based (latest instant)
    eq = instant.get("stockholders_equity")
    total_debt = None
    debt_parts = [instant[k].value for k in ("long_term_debt_noncurrent", "debt_current", "short_term_borrowings") if k in instant]
    if debt_parts:
        total_debt = sum(debt_parts)
        end = max(instant[k].end for k in ("long_term_debt_noncurrent", "debt_current", "short_term_borrowings") if k in instant)
        add("total_debt", total_debt, "INSTANT", end, calc=True)
    if eq and total_debt is not None and eq.value > 0:
        add("debt_to_equity", total_debt / eq.value, "INSTANT", eq.end, calc=True, unit="ratio")
    if "current_assets" in instant and "current_liabilities" in instant:
        add("current_ratio", _safe_div(instant["current_assets"].value, instant["current_liabilities"].value), "INSTANT", instant["current_assets"].end, calc=True, unit="ratio")
    # TTM-based returns on capital
    if "net_income" in ttm and eq and eq.value > 0:
        add("roe", _pct(ttm["net_income"][0] / eq.value), "TTM", ttm["net_income"][1], calc=True, unit="%")
    if "net_income" in ttm and "total_assets" in instant:
        add("roa", _pct(_safe_div(ttm["net_income"][0], instant["total_assets"].value)), "TTM", ttm["net_income"][1], calc=True, unit="%")
    if "operating_income" in ttm and "interest_expense" in ttm and ttm["interest_expense"][0] > 0:
        add("interest_coverage", ttm["operating_income"][0] / ttm["interest_expense"][0], "TTM", ttm["operating_income"][1], calc=True, unit="x")
    if "operating_cash_flow" in ttm and "capex" in ttm:
        fcf_ttm = ttm["operating_cash_flow"][0] - ttm["capex"][0]
        add("free_cash_flow", fcf_ttm, "TTM", ttm["operating_cash_flow"][1], calc=True)
        if "revenue" in ttm:
            add("fcf_margin", _pct(_safe_div(fcf_ttm, ttm["revenue"][0])), "TTM", ttm["revenue"][1], calc=True, unit="%")
    for m_num, name in (("operating_income", "operating_margin"), ("net_income", "net_margin")):
        if m_num in ttm and "revenue" in ttm and ttm[m_num][1] == ttm["revenue"][1]:
            add(name, _pct(_safe_div(ttm[m_num][0], ttm["revenue"][0])), "TTM", ttm["revenue"][1], calc=True, unit="%")
    return rows


def _pct(x: float | None) -> float | None:
    return None if x is None else x * 100


def _growth(add, name: str, ser: dict) -> None:
    _growth_values(add, name, {end: f.value for end, f in ser.items()})


def _growth_values(add, name: str, values: dict[date, float]) -> None:
    ends = sorted(values)
    for i, end in enumerate(ends):
        prev = [e for e in ends[:i] if 350 <= (end - e).days <= 380]
        if not prev:
            continue
        p = values[prev[-1]]
        if p is None or p <= 0:  # growth from a negative/zero base is not meaningful
            continue
        add(name, (values[end] / p - 1) * 100, "FY", end, calc=True, unit="%")


# --- persistence --------------------------------------------------------------
def store_metrics(db: Session, stock_id: int, rows: list[dict], provider: str = "sec_edgar") -> int:
    if not rows:
        return 0
    now = datetime.now(UTC)
    payload = [{**r, "stock_id": stock_id, "provider": provider, "retrieved_at": now} for r in rows]
    # de-duplicate on the unique key (last wins)
    uniq = {}
    for r in payload:
        uniq[(r["metric"], r["period_end"], r["period_type"])] = r
    payload = list(uniq.values())
    table = FundamentalData.__table__
    stmt = insert(table).values(payload)
    cols = ["value", "unit", "period_start", "fiscal_year", "fiscal_period", "form", "filed_date", "accession_number", "is_calculated", "source_tag", "provider", "retrieved_at"]
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fundamental_metric_period",
        set_={c: stmt.excluded[c] for c in cols},
        where=table.c.value.is_distinct_from(stmt.excluded.value) | table.c.filed_date.is_distinct_from(stmt.excluded.filed_date),
    )
    return len(db.execute(stmt.returning(table.c.id)).all())


def refresh_fundamentals(db: Session, stock: Stock) -> dict:
    """Fetch SEC company facts + recent filings for one stock."""
    if stock.asset_type in ("ETF", "INDEX", "FUND"):
        return {"symbol": stock.symbol, "status": "SKIPPED", "reason": f"{stock.asset_type} has no SEC company financials"}
    sec = registry.get_provider("sec_edgar")
    try:
        if not stock.cik:
            stock.cik = sec.lookup_cik(stock.symbol)  # type: ignore[attr-defined]
            registry.record_success(db, sec.name)
        if not stock.cik:
            return {"symbol": stock.symbol, "status": "NO_CIK"}
        facts = sec.get_company_facts(stock.cik)  # type: ignore[attr-defined]
        filings = sec.recent_filings(stock.cik)  # type: ignore[attr-defined]
        registry.record_success(db, sec.name, requests=2)
    except ProviderError as exc:
        registry.record_failure(db, sec.name, exc)
        return {"symbol": stock.symbol, "status": "FAILED", "error": str(exc)[:300]}
    ext = extract_fundamentals(facts)
    rows = derive_metrics(ext)
    n = store_metrics(db, stock.id, rows)
    if "shares_outstanding" in ext["instant"]:
        stock.shares_outstanding = ext["instant"]["shares_outstanding"].value
    new_filings = store_filings(db, stock, filings)
    return {"symbol": stock.symbol, "status": "UPDATED", "metrics_written": n, "new_filings": new_filings}


def store_filings(db: Session, stock: Stock, filings: list[dict]) -> list[dict]:
    if not filings:
        return []
    existing = set(db.scalars(select(SecFiling.accession_number).where(SecFiling.stock_id == stock.id)))
    first_load = not existing
    new = []
    for f in filings:
        if f["accession_number"] in existing:
            continue
        db.add(
            SecFiling(
                stock_id=stock.id,
                cik=stock.cik,
                accession_number=f["accession_number"],
                form=f["form"],
                filing_date=f["filing_date"],
                report_date=f["report_date"],
                primary_document=f["primary_document"],
                url=f["url"],
                notified=first_load,  # don't alert on historical backfill
            )
        )
        if not first_load:
            new.append(f)
    db.flush()
    return new


# --- read model ---------------------------------------------------------------
KEY_METRICS = [
    "revenue", "revenue_growth_yoy", "gross_margin", "operating_margin", "net_margin", "roe", "roa",
    "total_debt", "cash", "debt_to_equity", "current_ratio", "interest_coverage",
    "operating_cash_flow", "free_cash_flow", "fcf_margin", "eps_diluted", "eps_growth_yoy", "fcf_growth_yoy",
]


def latest_snapshot(db: Session, stock_id: int) -> FundamentalSnapshot:
    rows = db.scalars(select(FundamentalData).where(FundamentalData.stock_id == stock_id)).all()
    if not rows:
        return FundamentalSnapshot(as_of=None, missing=list(KEY_METRICS))
    pref = {"TTM": 3, "INSTANT": 2, "FY": 1}
    best: dict[str, FundamentalData] = {}
    for r in rows:
        cur = best.get(r.metric)
        if cur is None or (r.period_end, pref.get(r.period_type, 0)) > (cur.period_end, pref.get(cur.period_type, 0)):
            best[r.metric] = r
    values = {
        m: {
            "value": r.value,
            "unit": r.unit,
            "period_type": r.period_type,
            "period_end": r.period_end.isoformat(),
            "fiscal_year": r.fiscal_year,
            "form": r.form,
            "filed_date": r.filed_date.isoformat() if r.filed_date else None,
            "is_calculated": r.is_calculated,
            "source": r.source_tag,
            "provider": r.provider,
        }
        for m, r in best.items()
    }
    as_of = max(r.period_end for r in best.values())
    return FundamentalSnapshot(as_of=as_of, values=values, missing=[m for m in KEY_METRICS if m not in values])


def valuation(snapshot: FundamentalSnapshot, price: float | None, forward_pe: float | None = None) -> dict:
    """Price-based valuation multiples. Not meaningful values (negative earnings) are None with a reason."""
    v = snapshot.values

    def val(m):
        return v.get(m, {}).get("value")

    out: dict[str, dict] = {}
    shares = val("shares_outstanding")
    mcap = price * shares if price and shares else None
    out["market_cap"] = {"value": mcap, "basis": "close x SEC shares outstanding" if mcap else None}
    eps = val("eps_diluted")
    if price and eps is not None:
        out["pe"] = {"value": price / eps if eps > 0 else None, "basis": f"{v['eps_diluted']['period_type']} diluted EPS", "note": None if eps > 0 else "negative earnings: not meaningful"}
    else:
        out["pe"] = {"value": None, "note": "EPS unavailable"}
    out["forward_pe"] = {"value": forward_pe, "basis": "provider estimate (Yahoo)" if forward_pe else None, "note": None if forward_pe else "unavailable"}
    rev = val("revenue")
    out["ps"] = {"value": mcap / rev if mcap and rev and rev > 0 else None, "basis": "market cap / revenue"}
    eq = val("stockholders_equity")
    out["pb"] = {"value": mcap / eq if mcap and eq and eq > 0 else None, "basis": "market cap / equity", "note": None if (eq or 0) > 0 else "equity unavailable or negative"}
    op, da = val("operating_income"), val("depreciation_amortization")
    debt, cash = val("total_debt"), val("cash")
    if mcap and op is not None and da is not None and debt is not None and cash is not None and (op + da) > 0:
        out["ev_ebitda"] = {"value": (mcap + debt - cash) / (op + da), "basis": "EV / (operating income + D&A)"}
    else:
        out["ev_ebitda"] = {"value": None, "note": "insufficient reliable data"}
    return out


def fundamental_summary(snapshot: FundamentalSnapshot) -> list[str]:
    v = snapshot.values

    def val(m):
        return v.get(m, {}).get("value")

    lines: list[str] = []
    g = val("revenue_growth_yoy")
    if g is not None:
        lines.append(f"Revenue {'grew' if g >= 0 else 'declined'} {abs(g):.1f}% year over year (FY {v['revenue_growth_yoy']['period_end'][:4]}).")
    om = val("operating_margin")
    if om is not None:
        lines.append(f"Operating margin {om:.1f}% ({v['operating_margin']['period_type']}).")
    fcf = val("free_cash_flow")
    if fcf is not None:
        lines.append(f"Free cash flow is {'positive' if fcf > 0 else 'negative'} ({fcf / 1e9:,.2f}B USD, {v['free_cash_flow']['period_type']}).")
    de = val("debt_to_equity")
    if de is not None:
        lines.append(f"Debt-to-equity {de:.2f}.")
    cr = val("current_ratio")
    if cr is not None:
        lines.append(f"Current ratio {cr:.2f}.")
    if snapshot.missing:
        lines.append(f"Unavailable: {', '.join(snapshot.missing)}.")
    return lines


def detect_deterioration(db: Session, stock_id: int) -> list[str]:
    """Compare the two latest fiscal years for significant fundamental deterioration."""
    out: list[str] = []
    rows = db.scalars(
        select(FundamentalData).where(FundamentalData.stock_id == stock_id, FundamentalData.period_type == "FY").order_by(FundamentalData.period_end)
    ).all()
    by_metric: dict[str, list[FundamentalData]] = {}
    for r in rows:
        by_metric.setdefault(r.metric, []).append(r)
    g = by_metric.get("revenue_growth_yoy", [])
    if len(g) >= 2 and g[-1].value is not None and g[-2].value is not None and g[-1].value < 0 <= g[-2].value:
        out.append(f"Revenue growth turned negative ({g[-1].value:.1f}% vs {g[-2].value:.1f}%).")
    fcf = by_metric.get("free_cash_flow", [])
    if len(fcf) >= 2 and fcf[-1].value is not None and fcf[-2].value is not None and fcf[-1].value < 0 <= fcf[-2].value:
        out.append("Free cash flow became negative.")
    om = by_metric.get("operating_margin", [])
    if len(om) >= 2 and om[-1].value is not None and om[-2].value is not None and om[-2].value - om[-1].value >= 5:
        out.append(f"Operating margin fell {om[-2].value - om[-1].value:.1f} pts.")
    return out


__all__ = [
    "extract_fundamentals",
    "derive_metrics",
    "refresh_fundamentals",
    "latest_snapshot",
    "valuation",
    "fundamental_summary",
    "detect_deterioration",
]
