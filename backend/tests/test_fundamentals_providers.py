"""Fundamentals extraction (SEC XBRL shapes), provider parsing and backtest bias controls."""

from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from app.analysis.fundamentals import compute_ttm, derive_metrics, extract_fundamentals, valuation, FundamentalSnapshot
from app.backtest.engine import StrategyConfig, run_backtest
from app.core.cache import reset_backend
from app.market import calendar
from app.providers.alpha_vantage import AlphaVantageProvider
from app.providers.base import NotSupported, RateLimited, SymbolNotFound
from app.providers.sec_edgar import SecEdgarProvider
from app.providers.yahoo import normalize_yf_frame
from tests.conftest import synthetic_ohlcv


def fact(val, start, end, form, filed, fy=None, fp="FY", accn="0000000000-00-000001"):
    return {"val": val, "start": start, "end": end, "form": form, "filed": filed, "fy": fy, "fp": fp, "accn": accn}


def inst(val, end, form, filed):
    return {"val": val, "end": end, "form": form, "filed": filed, "fy": 2025, "fp": "Q2", "accn": "x"}


COMPANY_FACTS = {
    "cik": 1,
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"val": 1_000_000_000, "end": "2026-07-15", "filed": "2026-08-01", "form": "10-Q"}]}}},
        "us-gaap": {
            # Old tag used until FY2023, new tag afterwards (tag switching)
            "Revenues": {"units": {"USD": [fact(80e9, "2022-01-01", "2022-12-31", "10-K", "2023-02-01", 2022), fact(90e9, "2023-01-01", "2023-12-31", "10-K", "2024-02-01", 2023)]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        fact(90e9, "2023-01-01", "2023-12-31", "10-K", "2025-02-01", 2024),  # comparative in later 10-K
                        fact(100e9, "2024-01-01", "2024-12-31", "10-K", "2025-02-01", 2024),
                        fact(110e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01", 2025),
                        # 6-month YTD current and prior year (10-Q)
                        fact(60e9, "2026-01-01", "2026-06-30", "10-Q", "2026-08-01", 2026, "Q2"),
                        fact(52e9, "2025-01-01", "2025-06-30", "10-Q", "2025-08-01", 2025, "Q2"),
                        # a 3-month quarter that must not be mistaken for an annual value
                        fact(31e9, "2026-04-01", "2026-06-30", "10-Q", "2026-08-01", 2026, "Q2"),
                    ]
                }
            },
            "OperatingIncomeLoss": {"units": {"USD": [fact(30e9, "2024-01-01", "2024-12-31", "10-K", "2025-02-01"), fact(33e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01")]}},
            "NetIncomeLoss": {"units": {"USD": [fact(20e9, "2024-01-01", "2024-12-31", "10-K", "2025-02-01"), fact(22e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01")]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [fact(35e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01")]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [fact(10e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01")]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [fact(2.0, "2024-01-01", "2024-12-31", "10-K", "2025-02-01"), fact(2.2, "2025-01-01", "2025-12-31", "10-K", "2026-02-01")]}},
            "StockholdersEquity": {"units": {"USD": [inst(80e9, "2025-12-31", "10-K", "2026-02-01"), inst(88e9, "2026-06-30", "10-Q", "2026-08-01")]}},
            "AssetsCurrent": {"units": {"USD": [inst(50e9, "2026-06-30", "10-Q", "2026-08-01")]}},
            "LiabilitiesCurrent": {"units": {"USD": [inst(25e9, "2026-06-30", "10-Q", "2026-08-01")]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [inst(40e9, "2026-06-30", "10-Q", "2026-08-01")]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [inst(20e9, "2026-06-30", "10-Q", "2026-08-01")]}},
        },
    },
}


def test_extract_annual_ttm_and_instant():
    ext = extract_fundamentals(COMPANY_FACTS)
    rev = ext["annual"]["revenue"]
    assert [f.end.year for f in rev] == [2022, 2023, 2024, 2025]
    assert rev[-1].value == 110e9
    # TTM = FY2025 + YTD 2026 - YTD 2025
    val, end, basis = ext["ttm"]["revenue"]
    assert basis == "TTM" and end == date(2026, 6, 30) and val == pytest.approx(110e9 + 60e9 - 52e9)
    # instant uses the latest balance sheet
    assert ext["instant"]["stockholders_equity"].value == 88e9
    assert ext["instant"]["shares_outstanding"].value == 1e9


def test_derived_metrics_reported_vs_calculated():
    rows = derive_metrics(extract_fundamentals(COMPANY_FACTS))
    by = {(r["metric"], r["period_type"], r["period_end"]): r for r in rows}
    g = by[("revenue_growth_yoy", "FY", date(2025, 12, 31))]
    assert g["is_calculated"] and g["value"] == pytest.approx(10.0)
    assert by[("revenue", "FY", date(2025, 12, 31))]["is_calculated"] is False
    assert by[("operating_margin", "FY", date(2025, 12, 31))]["value"] == pytest.approx(30.0)
    assert by[("free_cash_flow", "FY", date(2025, 12, 31))]["value"] == pytest.approx(25e9)
    assert by[("current_ratio", "INSTANT", date(2026, 6, 30))]["value"] == pytest.approx(2.0)
    assert by[("debt_to_equity", "INSTANT", date(2026, 6, 30))]["value"] == pytest.approx(40 / 88)
    # FY2024 FCF missing capex -> no FCF row (missing, not zero)
    assert ("free_cash_flow", "FY", date(2024, 12, 31)) not in by
    # interest coverage missing interest expense -> absent
    assert not any(r["metric"] == "interest_coverage" for r in rows)


def test_ttm_falls_back_to_fy_without_matching_prior_ytd():
    ext = extract_fundamentals({"facts": {"us-gaap": {"Revenues": {"units": {"USD": [fact(10.0, "2025-01-01", "2025-12-31", "10-K", "2026-02-01"), fact(6.0, "2026-01-01", "2026-06-30", "10-Q", "2026-08-01")]}}}}})
    assert ext["ttm"]["revenue"] == (10.0, date(2025, 12, 31), "FY")


def test_valuation_negative_earnings_not_meaningful():
    snap = FundamentalSnapshot(as_of=None, values={"eps_diluted": {"value": -1.0, "period_type": "TTM"}, "shares_outstanding": {"value": 100.0}, "revenue": {"value": 1000.0}})
    v = valuation(snap, 10.0)
    assert v["pe"]["value"] is None and "not meaningful" in v["pe"]["note"]
    assert v["ps"]["value"] == pytest.approx(1.0)
    assert v["ev_ebitda"]["value"] is None


# --- providers ---------------------------------------------------------------------------------
def test_yahoo_normalization():
    idx = pd.DatetimeIndex(["2026-01-05", "2026-01-06"]).tz_localize("America/New_York")
    raw = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0.5, 1], "Close": [1.5, 2.5], "Adj Close": [1.4, 2.4], "Volume": [100, 200], "Dividends": [0.0, 0.2], "Stock Splits": [0.0, 0.0]}, index=idx)
    df = normalize_yf_frame(raw)
    assert list(df.index) == [date(2026, 1, 5), date(2026, 1, 6)]
    assert pd.isna(df.loc[date(2026, 1, 5), "dividend"]) and df.loc[date(2026, 1, 6), "dividend"] == 0.2
    assert df["split_ratio"].isna().all()


@respx.mock
def test_alpha_vantage_parsing_and_limits(monkeypatch):
    reset_backend(memory=True)
    monkeypatch.setattr("app.providers.http._throttle", lambda *a, **k: None)
    av = AlphaVantageProvider(api_key="demo")
    av.min_interval = 0
    respx.get("https://www.alphavantage.co/query", params={"function": "TIME_SERIES_DAILY"}).mock(
        return_value=httpx.Response(200, json={"Meta Data": {}, "Time Series (Daily)": {"2026-01-06": {"1. open": "2", "2. high": "3", "3. low": "1", "4. close": "2.5", "5. volume": "200"}, "2026-01-05": {"1. open": "1", "2. high": "2", "3. low": "0.5", "4. close": "1.5", "5. volume": "100"}}})
    )
    res = av.get_daily_history("IBM", date(2026, 1, 1), date(2026, 1, 31))
    assert not res.adjusted_included and len(res.frame) == 2 and res.frame["volume"].iloc[-1] == 200
    respx.get("https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE"}).mock(return_value=httpx.Response(200, json={"Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}))
    with pytest.raises(RateLimited):
        av.get_quote("IBM")
    respx.get("https://www.alphavantage.co/query", params={"function": "OVERVIEW"}).mock(return_value=httpx.Response(200, json={"Error Message": "Invalid API call."}))
    with pytest.raises(SymbolNotFound):
        av.get_metadata("NOPE")
    # daily budget enforced locally
    av.daily_limit = av.quota_used_today()
    with pytest.raises(RateLimited):
        av.get_earnings("IBM")
    with pytest.raises(NotSupported):
        AlphaVantageProvider(api_key="").get_quote("IBM")


@respx.mock
def test_sec_ticker_map_and_filings(monkeypatch):
    reset_backend(memory=True)
    monkeypatch.setattr("app.providers.http._throttle", lambda *a, **k: None)
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 1067983, "ticker": "BRK.B", "title": "Berkshire"}}))
    route = respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json={"filings": {"recent": {"accessionNumber": ["0000320193-26-000010", "0000320193-26-000009"], "form": ["10-Q", "4"], "filingDate": ["2026-08-01", "2026-07-01"], "reportDate": ["2026-06-27", ""], "primaryDocument": ["aapl-20260627.htm", "x.xml"]}}})
    )
    sec = SecEdgarProvider()
    assert sec.lookup_cik("AAPL") == "0000320193" and sec.lookup_cik("BRK-B") == "0001067983"
    f = sec.recent_filings("0000320193")
    assert len(f) == 1 and f[0]["form"] == "10-Q" and f[0]["url"].endswith("/000032019326000010/aapl-20260627.htm")
    assert "StockMonitor" in route.calls.last.request.headers["user-agent"]  # SEC requires a descriptive UA


# --- backtest --------------------------------------------------------------------------------------
def _daily(n_years=4, seed=3, drift=0.0008):
    end = calendar.latest_completed_session()
    days = calendar.trading_days(end - timedelta(days=365 * n_years), end)
    return synthetic_ohlcv(days, seed=seed, drift=drift)


def test_backtest_no_lookahead_and_costs():
    df = _daily()
    cfg = StrategyConfig(require_macd_bull_cross=False, rsi_min=0, rsi_max=100, stop_loss_pct=None)
    res = run_backtest("TST", df, cfg)
    # entries happen at the open of the session AFTER the signal
    for t in res.trades:
        assert t.entry_price == pytest.approx(df.loc[t.entry_date, "open"] * (1 + (cfg.commission_bps + cfg.slippage_bps) / 1e4))
    # truncating future data does not change earlier trades
    cut = df.index[int(len(df) * 0.8)]
    res2 = run_backtest("TST", df[df.index <= cut], cfg)
    early = [(t.entry_date, round(t.entry_price, 8)) for t in res.trades if t.exit_date and t.exit_date < cut - timedelta(days=10)]
    early2 = [(t.entry_date, round(t.entry_price, 8)) for t in res2.trades if t.exit_date and t.exit_date < cut - timedelta(days=10)]
    assert early == early2
    # higher costs -> lower final equity
    hi = run_backtest("TST", df, cfg.model_copy(update={"commission_bps": 50, "slippage_bps": 50}))
    if res.metrics["number_of_trades"]:
        assert hi.metrics["final_equity"] < res.metrics["final_equity"]


def test_backtest_dividend_adjustment():
    df = _daily()
    df["adj_close"] = df["close"] * np.linspace(0.9, 1.0, len(df))  # dividends embedded in adj series
    res = run_backtest("TST", df, StrategyConfig(require_macd_bull_cross=False, rsi_min=0, rsi_max=100))
    bh = res.benchmarks["TST buy & hold"]["total_return_pct"]
    start, end = res.start, res.end
    expected = (df.loc[end, "adj_close"] / df.loc[start, "adj_close"] - 1) * 100
    assert bh == pytest.approx(expected, rel=1e-6)
