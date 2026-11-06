from datetime import timedelta

from app.integrations.line.validator import validate_messages
from app.market import calendar
from app.snapshot import build_snapshot, line_messages
from tests.conftest import synthetic_ohlcv


def test_snapshot_and_line_message():
    latest = calendar.latest_completed_session()
    days = calendar.trading_days(latest - timedelta(days=900), latest)
    frames = {s: synthetic_ohlcv(days, seed=i, drift=d) for i, (s, d) in enumerate([("UP", 0.0015), ("DOWN", -0.0012), ("SPY", 0.0005)])}
    frames["OLD"] = frames["UP"].iloc[:-3]  # stale symbol
    cfg = {"symbols": ["UP", "DOWN", "OLD"], "benchmark": "SPY"}
    prev = {"session": "2000-01-03", "stocks": [{"symbol": "UP", "signal": "BEARISH"}], "line_sent_session": "2000-01-03"}
    snap = build_snapshot(cfg, frames, latest, {}, prev)
    by = {s["symbol"]: s for s in snap["stocks"]}
    assert by["OLD"]["status"] == "STALE" and "signal" not in by["OLD"]  # no signal from stale data
    assert by["UP"]["status"] == "OK" and by["UP"]["changed"] and by["UP"]["coverage"] < 1
    assert snap["next_session"] > snap["session"]
    msgs = line_messages(snap, "https://example.github.io/repo")
    assert len(msgs) == 1 and validate_messages(msgs) == []
