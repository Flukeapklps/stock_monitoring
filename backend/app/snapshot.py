"""Free daily snapshot mode (GitHub Actions + GitHub Pages, no database).

    python -m app.snapshot --config ../snapshot.config.json --out ../site/data

1. Downloads ~3 years of daily bars (one batched Yahoo request) for the watchlist + benchmark.
2. Validates them and computes indicators, rules and the investment score (same code as the full platform).
3. Optionally adds SEC fundamentals (one company-facts request per equity).
4. Writes `latest.json` for the static dashboard.
5. Sends ONE LINE message per trading session with tomorrow's plan
   (needs LINE_CHANNEL_ACCESS_TOKEN + LINE_USER_ID; skipped if the session was already sent).

The previous snapshot (`--previous`, fetched from the live Pages site) is used to detect signal
changes and to avoid sending the same session twice.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.analysis.fundamentals import derive_metrics, extract_fundamentals
from app.analysis.rules import evaluate
from app.analysis.scoring import compute_score
from app.core.cache import reset_backend
from app.indicators.compute import compute_daily, compute_weekly, row_to_json
from app.market import calendar
from app.pipeline.quality import validate_ohlcv
from app.pipeline.resample import to_weekly

log = logging.getLogger("snapshot")

ACTIONS = {
    "ACCUMULATION_WATCH": "Consider accumulating",
    "STRONG_LONG_TERM_TREND": "Hold — trend strong",
    "BULLISH": "Hold — trend intact",
    "NEUTRAL": "Watch",
    "WEAKENING": "Review position",
    "BEARISH": "Avoid / review",
}
SNAP_KEYS = ("close", "rsi_14", "sma_50", "sma_200", "return_1d", "return_1y", "dist_52w_high_pct", "macd_hist", "adx")
FUND_PREF = {"TTM": 3, "INSTANT": 2, "FY": 1}


def _fundamentals(symbols: list[str]) -> dict[str, dict]:
    """{symbol: snapshot values} from SEC company facts; failures are skipped (missing, not zero)."""
    from app.providers.sec_edgar import SecEdgarProvider

    sec, out = SecEdgarProvider(), {}
    for sym in symbols:
        try:
            cik = sec.lookup_cik(sym)
            if not cik:
                continue
            rows = derive_metrics(extract_fundamentals(sec.get_company_facts(cik)))
        except Exception as exc:  # noqa: BLE001 - fundamentals are optional
            log.warning("fundamentals %s: %s", sym, exc)
            if "403" in str(exc):
                log.error("SEC EDGAR refused access (403): set the SEC_USER_AGENT repository variable to "
                          "'AppName/1.0 your-email@example.com'. Skipping fundamentals for all symbols.")
                break
            continue
        best: dict[str, dict] = {}
        for r in rows:
            cur = best.get(r["metric"])
            if cur is None or (r["period_end"], FUND_PREF.get(r["period_type"], 0)) > (cur["period_end"], FUND_PREF.get(cur["period_type"], 0)):
                best[r["metric"]] = r
        out[sym] = {m: {"value": r["value"], "period_type": r["period_type"], "period_end": r["period_end"].isoformat()} for m, r in best.items()}
    return out


def analyze(sym: str, frame: pd.DataFrame, latest, bench_daily: dict | None, fundamentals: dict | None, asset_type: str = "EQUITY") -> dict:
    rep = validate_ohlcv(frame, asset_type=asset_type, latest_session=latest)
    df = rep.frame
    if df.empty:
        return {"symbol": sym, "status": "NO_DATA", "issues": [i.message for i in rep.issues]}
    last = max(df.index)
    if last < latest:
        return {"symbol": sym, "status": "STALE", "trade_date": last.isoformat(), "issues": [i.message for i in rep.issues]}
    daily_ind = compute_daily(df)
    d = row_to_json(daily_ind.iloc[-1])
    w_ind = compute_weekly(to_weekly(df, latest))
    weekly = None
    for _, row in w_ind.iloc[::-1].iterrows():
        if bool(row.get("is_complete", True)):
            weekly = row_to_json(row)
            break
    rel = {"benchmark": "SPY"}
    if bench_daily:
        for k in ("6m", "1y"):
            a, b = d.get(f"return_{k}"), bench_daily.get(f"return_{k}")
            rel[f"rel_{k}"] = None if a is None or b is None else round(a - b, 2)
    ev = evaluate(d, weekly, None, None)
    score = compute_score(d, weekly, fundamentals or {}, rel)
    signal = "ACCUMULATION_WATCH" if ev.accumulation else score.classification
    flags = (["TREND_WEAKENING"] if ev.risk else []) + (["OVERBOUGHT"] if ev.overbought else [])
    if ev.accumulation:
        reasons = [c.label for c in ev.accumulation_conditions if c.passed]
    elif ev.risk:
        reasons = [c.label for c in ev.risk_conditions if c.passed]
    else:
        reasons = [c.label for c in ev.trend_conditions if c.passed]
    action = ACTIONS.get(signal or "", "Watch")
    if "TREND_WEAKENING" in flags and signal not in ("WEAKENING", "BEARISH"):
        action = "Review position"
    elif "OVERBOUGHT" in flags and signal != "ACCUMULATION_WATCH":
        action += " · don't chase"
    closes = df["close"].astype(float).tail(130)
    return {
        "symbol": sym,
        "status": "OK",
        "trade_date": last.isoformat(),
        "signal": signal,
        "action": action,
        "flags": flags,
        "trend": ev.long_term_trend,
        "weekly_trend": ev.weekly_trend,
        "score": None if score.total is None else round(score.total, 1),
        "coverage": round(score.coverage, 2),
        "reasons": reasons[:5],
        "explanation": score.explanation,
        "indicators": {k: d.get(k) for k in SNAP_KEYS},
        "relative": rel,
        "spark": [round(x, 2) for x in closes.tolist()],
        "issues": [i.message for i in rep.issues if i.severity != "INFO"][:5],
    }


def build_snapshot(cfg: dict, frames: dict[str, pd.DataFrame], latest, fundamentals: dict, previous: dict | None) -> dict:
    bench = cfg.get("benchmark", "SPY")
    bench_entry = None
    if bench in frames and not frames[bench].empty:
        bdf = validate_ohlcv(frames[bench], asset_type="ETF", latest_session=latest).frame
        if not bdf.empty:
            bench_entry = row_to_json(compute_daily(bdf).iloc[-1])
    prev_sig = {s["symbol"]: s.get("signal") for s in (previous or {}).get("stocks", [])}
    stocks = []
    for sym in cfg["symbols"]:
        e = analyze(sym, frames.get(sym, pd.DataFrame()), latest, bench_entry, fundamentals.get(sym), cfg.get("asset_types", {}).get(sym, "EQUITY"))
        e["name"] = cfg.get("names", {}).get(sym)
        e["previous_signal"] = prev_sig.get(sym)
        e["changed"] = e.get("signal") is not None and prev_sig.get(sym) not in (None, e.get("signal"))
        stocks.append(e)
    stocks.sort(key=lambda s: (s["status"] != "OK", -(s.get("score") or 0)))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "session": latest.isoformat(),
        "next_session": calendar.next_session(latest).isoformat(),
        "provider": "Yahoo Finance (end-of-day, best-effort)" + (" + SEC EDGAR" if fundamentals else ""),
        "data_status": "END_OF_DAY",
        "benchmark": {"symbol": bench, "close": (bench_entry or {}).get("close"), "return_1d": (bench_entry or {}).get("return_1d")},
        "stocks": stocks,
        "line_sent_session": (previous or {}).get("line_sent_session"),
    }


def line_messages(snap: dict, site_url: str | None) -> list[dict]:
    """One compact Flex bubble: tomorrow's plan grouped by action."""
    from app.integrations.line import flex_templates as ft
    from app.integrations.line.validator import ensure_valid

    ok = [s for s in snap["stocks"] if s["status"] == "OK"]

    def line(s):
        return f"{s['symbol']} {ft.money(s['indicators']['close'])} ({ft.pct(s['indicators']['return_1d'])}) · score {s['score']:.0f}"

    groups = [
        ("🟢 Consider accumulating", [line(s) for s in ok if s["action"].startswith("Consider")]),
        ("🔴 Review / reduce risk", [line(s) for s in ok if s["action"].startswith(("Review", "Avoid"))]),
        ("🔁 Signal changes", [f"{s['symbol']}: {(s['previous_signal'] or '').replace('_', ' ')} → {s['signal'].replace('_', ' ')}" for s in ok if s["changed"]]),
        ("✅ Hold — trend intact", [s["symbol"] for s in ok if s["action"].startswith("Hold")]),
        ("👀 Watch", [s["symbol"] for s in ok if s["action"].startswith("Watch")]),
    ]
    sections = [(t, (items if t[0] in "🟢🔴🔁" else [", ".join(items)]) if items else []) for t, items in groups]
    problems = [f"{s['symbol']}: {s['status']}" for s in snap["stocks"] if s["status"] != "OK"]
    if problems:
        sections.append(("⚠️ Data issues (no signal)", problems))
    b = snap["benchmark"]
    rows = [(f"{b['symbol']} close", f"{ft.money(b['close'])} {ft.pct(b['return_1d'])}", ft.change_color(b["return_1d"]))]
    card = ft.table_card(
        f"📊 Plan for {snap['next_session']}",
        f"Based on close {snap['session']} · END_OF_DAY",
        rows,
        sections=[s for s in sections if s[1]],
        footer_note="Automated technical analysis, not a guaranteed investment recommendation.",
        links=[("Open Dashboard", site_url)] if site_url else None,
    )
    msgs = [ft.flex(f"Plan for {snap['next_session']}: " + "; ".join(f"{t.split(' ', 1)[1]} {len(i)}" for t, i in groups[:2]), card)]
    ensure_valid(msgs)
    return msgs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--previous", help="previous latest.json (optional)")
    p.add_argument("--no-line", action="store_true")
    p.add_argument("--force-line", action="store_true", help="send even if this session was already sent")
    p.add_argument("--min-ok-ratio", type=float, default=0.8, help="defer LINE if fewer symbols are fresh (a later run retries)")
    p.add_argument("--session", help="analyze this completed session (YYYY-MM-DD) instead of the latest")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    reset_backend(memory=True)  # no Redis in Actions

    cfg = json.loads(Path(a.config).read_text())
    previous = None
    if a.previous and Path(a.previous).is_file():
        try:
            previous = json.loads(Path(a.previous).read_text())
        except ValueError:
            previous = None

    latest = date.fromisoformat(a.session) if a.session else calendar.latest_completed_session(settle_minutes=60)
    if previous and previous.get("session") == latest.isoformat() and previous.get("line_sent_session") == latest.isoformat() and not a.force_line:
        log.info("snapshot for %s already published; nothing to do", latest)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        Path(a.out, "latest.json").write_text(json.dumps(previous))
        return 0

    from app.providers.yahoo import YahooFinanceProvider

    symbols = list(dict.fromkeys(cfg["symbols"] + [cfg.get("benchmark", "SPY")]))
    res = YahooFinanceProvider().get_daily_history_batch(symbols, latest - timedelta(days=365 * 3 + 30), latest)
    frames = {s: r.frame for s, r in res.items()}
    equities = [s for s in cfg["symbols"] if cfg.get("asset_types", {}).get(s, "EQUITY") == "EQUITY"]
    use_sec = cfg.get("fundamentals", True)
    if use_sec and not (os.environ.get("SEC_USER_AGENT") or "").strip():
        os.environ.pop("SEC_USER_AGENT", None)
        log.warning("SEC_USER_AGENT not set: skipping fundamentals (SEC requires 'AppName/1.0 your-email@example.com')")
        use_sec = False
    fundamentals = _fundamentals(equities) if use_sec else {}

    snap = build_snapshot(cfg, frames, latest, fundamentals, previous)
    token, user = (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip(), (os.environ.get("LINE_USER_ID") or "").strip()
    line_error = None
    already = snap["line_sent_session"] == snap["session"]
    ok_count = sum(1 for s in snap["stocks"] if s["status"] == "OK")
    if a.no_line or not token or not user:
        log.info("LINE not configured; skipping notification")
    elif already and not a.force_line:
        log.info("LINE already sent for %s", snap["session"])
    elif ok_count == 0 or ok_count < a.min_ok_ratio * len(snap["stocks"]):
        log.warning("only %d/%d symbols fresh; deferring LINE message to the next run", ok_count, len(snap["stocks"]))
    else:
        from app.integrations.line.client import LineClient

        from app.integrations.line.client import LineApiError

        try:
            resp = LineClient(access_token=token).push(user, line_messages(snap, cfg.get("site_url") or os.environ.get("SITE_URL")), retry_key=None)
            snap["line_sent_session"] = snap["session"]
            log.info("LINE message accepted (request id %s)", resp.request_id)
        except LineApiError as exc:
            hint = {
                401: "LINE_CHANNEL_ACCESS_TOKEN is invalid: use the long-lived Channel access token (Messaging API tab), not the Channel secret.",
                400: "LINE_USER_ID is invalid or the user has not added the Official Account as a friend.",
                429: "LINE rate limit or monthly message quota reached.",
            }.get(exc.status_code or 0, "")
            line_error = f"{exc} {hint}".strip()
            log.error("LINE send failed: %s", line_error)

    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, "latest.json").write_text(json.dumps(snap, default=str))
    log.info("wrote snapshot for %s: %d/%d symbols OK", snap["session"], ok_count, len(snap["stocks"]))
    if line_error:
        # Snapshot is still written and the site still deploys; the workflow fails at the end.
        Path(a.out).resolve().parent.parent.joinpath("line_error.txt").write_text(line_error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
