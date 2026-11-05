"""Historical backtesting engine for long-term rule strategies.

Bias controls:
  * Look-ahead: decisions use indicators computed from data up to the close of
    day t (all indicators are causal) and orders execute at the OPEN of t+1.
    Weekly rules only use weekly bars that were complete on day t.
  * Splits/dividends: the simulation runs on adjusted prices (OHLC scaled by
    adj_close/close), so dividends are reinvested and splits are neutral.
  * Costs: commission (bps of notional) + slippage (bps, adverse) per fill.
  * Survivorship: the universe is whatever symbols you test; delisted symbols are
    not included automatically. Results for today's winners overstate returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.indicators.compute import compute_daily, compute_weekly
from app.pipeline.resample import to_weekly


class StrategyConfig(BaseModel):
    # BUY rules
    require_price_above_sma200: bool = True
    require_sma50_above_sma200: bool = True
    rsi_min: float = 40.0
    rsi_max: float = 55.0
    require_macd_bull_cross: bool = True
    macd_cross_lookback: int = Field(3, ge=1, le=20, description="MACD bullish crossover within the last N sessions")
    # SELL / REVIEW rules
    exit_weekly_below_sma40: bool = True
    exit_trend_reversal: bool = True  # close < SMA200 and SMA50 < SMA200
    stop_loss_pct: float | None = Field(15.0, description="Exit when close falls this % below entry (risk threshold)")
    # costs
    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    initial_capital: float = 10_000.0
    risk_free_rate_pct: float = 0.0


@dataclass
class Trade:
    entry_date: date
    entry_price: float
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    shares: float = 0.0
    pnl: float | None = None
    return_pct: float | None = None
    holding_days: int | None = None


@dataclass
class BacktestResult:
    symbol: str
    start: date
    end: date
    metrics: dict
    trades: list[Trade]
    equity_curve: list[dict]
    benchmarks: dict
    assumptions: list[str] = field(default_factory=list)


def adjusted_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "adj_close" in out and out["adj_close"].notna().all():
        f = out["adj_close"] / out["close"]
        for c in ("open", "high", "low", "close"):
            out[c] = out[c] * f
    return out


def perf_metrics(equity: pd.Series, rf_pct: float = 0.0) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1
    rf_d = rf_pct / 100 / 252
    ex = rets - rf_d
    sharpe = float(ex.mean() / ex.std(ddof=1) * math.sqrt(252)) if ex.std(ddof=1) > 0 else None
    downside = ex[ex < 0]
    dd_std = math.sqrt((downside**2).sum() / len(ex)) if len(ex) else 0
    sortino = float(ex.mean() / dd_std * math.sqrt(252)) if dd_std > 0 else None
    peak = equity.cummax()
    mdd = float((equity / peak - 1).min())
    return {
        "total_return_pct": round(total * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "max_drawdown_pct": round(mdd * 100, 4),
        "sharpe_ratio": None if sharpe is None else round(sharpe, 4),
        "sortino_ratio": None if sortino is None else round(sortino, 4),
        "annualized_volatility_pct": round(float(rets.std(ddof=1) * math.sqrt(252) * 100), 4),
    }


def run_backtest(symbol: str, daily: pd.DataFrame, cfg: StrategyConfig, start: date | None = None, end: date | None = None, benchmarks: dict[str, pd.DataFrame] | None = None) -> BacktestResult:
    """daily: full stored history (indicators warm up on data before `start`)."""
    if daily.empty:
        raise ValueError("no price history")
    daily = daily.sort_index()
    adj = adjusted_ohlc(daily)
    ind = compute_daily(adj)
    weekly = to_weekly(adj, max(adj.index))
    wind = compute_weekly(weekly)
    # weekly SMA40 state known as of each daily date: last complete weekly bar <= date
    wstate = pd.Series(np.nan, index=adj.index, dtype=object)
    complete = wind[wind["is_complete"]] if "is_complete" in wind else wind
    w_idx = list(complete.index)
    j = -1
    for i, d in enumerate(adj.index):
        while j + 1 < len(w_idx) and w_idx[j + 1] <= d:
            j += 1
        if j >= 0:
            row = complete.iloc[j]
            if pd.notna(row["sma_40"]):
                wstate.iloc[i] = bool(row["close"] < row["sma_40"])

    start = start or adj.index[min(200, len(adj.index) - 1)]
    end = end or adj.index[-1]
    dates = [d for d in adj.index if start <= d <= end]
    if len(dates) < 2:
        raise ValueError("backtest window too short")

    cost = (cfg.commission_bps + cfg.slippage_bps) / 10_000
    cash = cfg.initial_capital
    shares = 0.0
    trades: list[Trade] = []
    open_trade: Trade | None = None
    pending: str | None = None  # "BUY" | ("SELL", reason)
    pending_reason = None
    equity = []
    bull_cross = ind["macd_bull_cross"].fillna(False).astype(bool).rolling(cfg.macd_cross_lookback, min_periods=1).max().astype(bool)

    for d in dates:
        o, c = float(adj.at[d, "open"]), float(adj.at[d, "close"])
        # 1) execute yesterday's decision at today's open
        if pending == "BUY" and shares == 0:
            px = o * (1 + cost)
            shares = cash / px
            cash = 0.0
            open_trade = Trade(entry_date=d, entry_price=px, shares=shares)
        elif pending == "SELL" and shares > 0 and open_trade:
            px = o * (1 - cost)
            cash = shares * px
            open_trade.exit_date, open_trade.exit_price, open_trade.exit_reason = d, px, pending_reason
            open_trade.pnl = (px - open_trade.entry_price) * open_trade.shares
            open_trade.return_pct = (px / open_trade.entry_price - 1) * 100
            open_trade.holding_days = (d - open_trade.entry_date).days
            trades.append(open_trade)
            open_trade, shares = None, 0.0
        pending, pending_reason = None, None
        equity.append((d, cash + shares * c))

        # 2) decide at today's close using information available at the close
        r = ind.loc[d]
        if shares == 0:
            ok = True
            if cfg.require_price_above_sma200:
                ok &= bool(pd.notna(r["sma_200"]) and r["close"] > r["sma_200"])
            if cfg.require_sma50_above_sma200:
                ok &= bool(pd.notna(r["sma_50"]) and pd.notna(r["sma_200"]) and r["sma_50"] > r["sma_200"])
            ok &= bool(pd.notna(r["rsi_14"]) and cfg.rsi_min <= r["rsi_14"] <= cfg.rsi_max)
            if cfg.require_macd_bull_cross:
                ok &= bool(bull_cross.loc[d])
            if ok and d != dates[-1]:
                pending = "BUY"
        else:
            reason = None
            if cfg.stop_loss_pct is not None and open_trade and c <= open_trade.entry_price * (1 - cfg.stop_loss_pct / 100):
                reason = f"Risk threshold: close {cfg.stop_loss_pct:.0f}% below entry"
            elif cfg.exit_weekly_below_sma40 and wstate.loc[d] is True:
                reason = "Weekly close below SMA 40"
            elif cfg.exit_trend_reversal and pd.notna(r["sma_200"]) and r["close"] < r["sma_200"] and r["sma_50"] < r["sma_200"]:
                reason = "Long-term trend reversal (below SMA 200, SMA 50 < SMA 200)"
            if reason and d != dates[-1]:
                pending, pending_reason = "SELL", reason

    eq = pd.Series({d: v for d, v in equity})
    eq.index = pd.to_datetime(eq.index)
    if open_trade:
        last_c = float(adj.at[dates[-1], "close"])
        open_trade.exit_reason = "Open at end of test (marked to market)"
        open_trade.pnl = (last_c - open_trade.entry_price) * open_trade.shares
        open_trade.return_pct = (last_c / open_trade.entry_price - 1) * 100
        open_trade.holding_days = (dates[-1] - open_trade.entry_date).days
        trades.append(open_trade)

    m = perf_metrics(eq, cfg.risk_free_rate_pct)
    closed = [t for t in trades if t.pnl is not None]
    wins = [t for t in closed if t.pnl > 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in closed if t.pnl <= 0)
    m.update(
        {
            "number_of_trades": len(trades),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else (None if not wins else float("inf")),
            "average_holding_days": round(float(np.mean([t.holding_days for t in closed if t.holding_days is not None])), 1) if closed else None,
            "exposure_pct": round(sum(t.holding_days or 0 for t in closed) / max((dates[-1] - dates[0]).days, 1) * 100, 2),
            "final_equity": round(float(eq.iloc[-1]), 2),
        }
    )
    if m.get("profit_factor") == float("inf"):
        m["profit_factor"] = None
        m["profit_factor_note"] = "no losing trades"

    bench_out: dict = {}
    sb = adj["close"].loc[[d for d in dates]]
    bh = pd.Series(sb.values / sb.iloc[0] * cfg.initial_capital, index=pd.to_datetime(dates))
    bench_out[f"{symbol} buy & hold"] = perf_metrics(bh, cfg.risk_free_rate_pct)
    curves = {f"{symbol} buy & hold": bh}
    for name, bdf in (benchmarks or {}).items():
        if bdf is None or bdf.empty:
            continue
        badj = adjusted_ohlc(bdf.sort_index())["close"]
        badj = badj[(badj.index >= dates[0]) & (badj.index <= dates[-1])]
        if len(badj) < 2:
            continue
        s = pd.Series(badj.values / badj.iloc[0] * cfg.initial_capital, index=pd.to_datetime(badj.index))
        bench_out[f"{name} buy & hold"] = perf_metrics(s, cfg.risk_free_rate_pct)
        curves[f"{name} buy & hold"] = s
    curve_df = pd.DataFrame({"strategy": eq, **curves}).ffill()
    step = max(1, len(curve_df) // 600)
    curve = [{"date": d.date().isoformat(), **{k: (None if pd.isna(v) else round(float(v), 2)) for k, v in row.items()}} for d, row in curve_df.iloc[::step].iterrows()]
    if curve and curve[-1]["date"] != curve_df.index[-1].date().isoformat():
        d = curve_df.index[-1]
        curve.append({"date": d.date().isoformat(), **{k: (None if pd.isna(v) else round(float(v), 2)) for k, v in curve_df.iloc[-1].items()}})
    return BacktestResult(
        symbol=symbol,
        start=dates[0],
        end=dates[-1],
        metrics=m,
        trades=trades,
        equity_curve=curve,
        benchmarks=bench_out,
        assumptions=[
            "Signals evaluated at the close; orders filled at the next session's open.",
            f"Costs per fill: commission {cfg.commission_bps} bps + slippage {cfg.slippage_bps} bps.",
            "Prices adjusted for splits and dividends (dividends reinvested).",
            "Weekly rules use only completed weekly bars.",
            "Survivorship bias: only the requested symbols are tested; delisted companies are not included.",
            "Past performance does not guarantee future results.",
        ],
    )
