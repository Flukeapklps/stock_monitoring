"""Indicator correctness against TA-Lib reference outputs (tests/fixtures/talib_reference.json,
generated with TA-Lib 0.6 on a deterministic random walk)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.indicators import core
from app.indicators.compute import compute_daily, row_to_json, support_resistance

FX = json.loads((Path(__file__).parent / "fixtures" / "talib_reference.json").read_text())
C = pd.Series(FX["input"]["close"], dtype=float)
H = pd.Series(FX["input"]["high"], dtype=float)
L = pd.Series(FX["input"]["low"], dtype=float)
V = pd.Series(FX["input"]["volume"], dtype=float)


def assert_matches(ours: pd.Series, ref_key: str, tol: float = 1e-6):
    ref = np.array([np.nan if x is None else x for x in FX[ref_key]], dtype=float)
    ours = np.asarray(ours, dtype=float)
    assert np.array_equal(np.isnan(ours), np.isnan(ref)), f"{ref_key}: NaN warm-up period differs"
    m = ~np.isnan(ref)
    assert np.allclose(ours[m], ref[m], atol=tol, rtol=1e-8), f"{ref_key}: max diff {np.max(np.abs(ours[m] - ref[m]))}"


def test_sma():
    assert_matches(core.sma(C, 20), "SMA_20")
    assert_matches(core.sma(C, 200), "SMA_200")


def test_ema():
    assert_matches(core.ema(C, 20), "EMA_20")
    assert_matches(core.ema(C, 50), "EMA_50")


def test_rsi():
    assert_matches(core.rsi(C, 14), "RSI_14")


def test_macd():
    m = core.macd(C, 12, 26, 9)
    assert_matches(m["macd"], "MACD")
    assert_matches(m["macd_signal"], "MACD_SIGNAL")
    assert_matches(m["macd_hist"], "MACD_HIST")


def test_bollinger():
    b = core.bollinger(C, 20, 2)
    assert_matches(b["bb_upper"], "BB_UPPER")
    assert_matches(b["bb_middle"], "BB_MIDDLE")
    assert_matches(b["bb_lower"], "BB_LOWER")


def test_atr():
    assert_matches(core.atr(H, L, C, 14), "ATR_14")


def test_adx():
    a = core.adx(H, L, C, 14)
    assert_matches(a["adx"], "ADX_14")
    assert_matches(a["plus_di"], "PLUS_DI_14")
    assert_matches(a["minus_di"], "MINUS_DI_14")


def test_roc_obv_ad():
    assert_matches(core.roc(C, 12), "ROC_12")
    assert_matches(core.obv(C, V), "OBV", tol=1e-3)
    assert_matches(core.ad_line(H, L, C, V), "AD", tol=1e-3)


def test_rsi_bounds_and_flat_series():
    flat = pd.Series([10.0] * 30)
    r = core.rsi(flat, 14)
    assert r.dropna().eq(50.0).all()
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert core.rsi(up, 14).dropna().eq(100.0).all()


def test_no_lookahead():
    """Indicators at bar i must not change when future bars are appended."""
    df = pd.DataFrame({"open": C, "high": H, "low": L, "close": C, "adj_close": C, "volume": V})
    full = compute_daily(df)
    partial = compute_daily(df.iloc[:250])
    cols = ["sma_50", "ema_50", "rsi_14", "macd", "adx", "atr_14", "bb_upper", "return_3m", "high_52w"]
    pd.testing.assert_frame_equal(full.iloc[:250][cols], partial[cols])


def test_row_to_json_handles_nan():
    out = row_to_json(pd.Series({"a": np.nan, "b": np.float64(1.23456789), "c": np.bool_(True), "d": np.int64(3)}))
    assert out == {"a": None, "b": 1.234568, "c": True, "d": 3}


def test_supertrend_direction():
    up = pd.Series(np.linspace(100, 200, 120))
    st = core.supertrend(up * 1.01, up * 0.99, up, 10, 3)
    assert st["supertrend_dir"].dropna().iloc[-1] == 1
    assert (st["supertrend"].dropna() < up[st["supertrend"].notna()]).all()


def test_support_resistance_levels():
    df = pd.DataFrame({"open": C, "high": H, "low": L, "close": C})
    sr = support_resistance(df)
    assert all(s["level"] < sr["reference_close"] for s in sr["support"])
    assert all(r["level"] > sr["reference_close"] for r in sr["resistance"])


@pytest.mark.parametrize("n", [1, 5, 30])
def test_short_series_do_not_crash(n):
    df = pd.DataFrame({"open": C[:n], "high": H[:n], "low": L[:n], "close": C[:n], "adj_close": C[:n], "volume": V[:n]})
    out = compute_daily(df)
    assert len(out) == n
    assert out["sma_200"].isna().all()
