from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.analysis.service import analyze_stocks
from app.core.security import ensure_admin_user, hash_password
from app.market import calendar
from app.models import Stock, User
from app.pipeline.ingestion import update_symbols


@pytest.fixture()
def client(db, fake_provider):
    from app.main import create_app

    ensure_admin_user(db)
    db.commit()
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-123"})
        assert r.status_code == 200
        yield c


@pytest.fixture()
def loaded(db, fake_provider):
    fake_provider.params["AAPL"] = {"drift": 0.0012}
    update_symbols(db, ["AAPL", "MSFT", "SPY", "QQQ"], years=3)
    analyze_stocks(db, list(db.query(Stock).all()))
    db.commit()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_auth_required(db, fake_provider):
    from app.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/v1/stocks").status_code == 401
        assert c.post("/api/v1/auth/login", json={"email": "x@y.z", "password": "bad"}).status_code == 401


def test_non_admin_forbidden_on_admin_endpoints(db, fake_provider):
    from app.main import create_app

    db.add(User(email="user@example.com", password_hash=hash_password("user-password-1"), role="user"))
    db.commit()
    with TestClient(create_app()) as c:
        c.post("/api/v1/auth/login", json={"email": "user@example.com", "password": "user-password-1"})
        assert c.get("/api/v1/auth/me").json()["role"] == "user"
        assert c.post("/api/v1/line/send", json={"text": "hi"}).status_code == 403
        assert c.post("/api/v1/system/jobs/daily_update/run").status_code == 403


def test_stock_endpoints(client, loaded):
    rows = client.get("/api/v1/stocks", params={"q": "AA"}).json()
    assert [r["symbol"] for r in rows] == ["AAPL"]
    r = rows[0]
    assert r["provider"] == "fake" and r["data_status"] == "END_OF_DAY" and r["price"] > 0
    d = client.get("/api/v1/stocks/AAPL").json()
    assert d["indicators"]["sma_200"] is not None
    assert d["score"]["total"] is not None and "explanation" in d["score"]
    assert d["freshness"]["status"] == "FRESH"
    bars = client.get("/api/v1/stocks/AAPL/bars", params={"timeframe": "weekly"}).json()
    assert bars["timeframe"] == "WEEKLY" and len(bars["bars"]) > 100
    ind = client.get("/api/v1/stocks/AAPL/indicators", params={"fields": "sma_50,rsi_14"}).json()
    assert set(ind["rows"][-1]) == {"date", "sma_50", "rsi_14"}
    assert client.get("/api/v1/stocks/AAPL/signals").json()
    rel = client.get("/api/v1/stocks/AAPL/relative").json()
    assert rel["rs_line"] and "rel_1y" in rel["summary"]
    assert client.get("/api/v1/stocks/NOPE").status_code == 404
    assert client.get("/api/v1/stocks/bad;sym").status_code == 422


def test_watchlist_crud(client, loaded):
    wl = client.get("/api/v1/watchlists").json()[0]
    assert client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "aapl"}).json()["symbol"] == "AAPL"
    detail = client.get(f"/api/v1/watchlists/{wl['id']}").json()
    assert [r["symbol"] for r in detail["rows"]] == ["AAPL"]
    assert client.post(f"/api/v1/watchlists/{wl['id']}/items", json={"symbol": "ZZZZ"}).status_code == 422
    assert client.delete(f"/api/v1/watchlists/{wl['id']}/items/AAPL").status_code == 200
    new = client.post("/api/v1/watchlists", json={"name": "Tech"}).json()
    assert client.post("/api/v1/watchlists", json={"name": "Tech"}).status_code == 409
    assert client.delete(f"/api/v1/watchlists/{new['id']}").status_code == 200


def test_portfolio_flow(client, loaded):
    pid = client.post("/api/v1/portfolios", json={"name": "Core"}).json()["id"]
    start = calendar.trading_days(date.today() - timedelta(days=200), date.today() - timedelta(days=150))[0]
    assert client.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "AAPL", "tx_type": "BUY", "trade_date": start.isoformat(), "quantity": 10, "price": 100, "fees": 1}).status_code == 200
    assert client.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "AAPL", "tx_type": "SELL", "trade_date": (start + timedelta(days=30)).isoformat(), "quantity": 4, "price": 120, "fees": 1}).status_code == 200
    # cannot oversell
    assert client.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "AAPL", "tx_type": "SELL", "trade_date": (start + timedelta(days=40)).isoformat(), "quantity": 100, "price": 120}).status_code == 422
    assert client.post(f"/api/v1/portfolios/{pid}/holdings", json={"symbol": "MSFT", "quantity": 5, "average_cost": 90, "purchase_date": start.isoformat()}).status_code == 200
    p = client.get(f"/api/v1/portfolios/{pid}").json()
    aapl = next(h for h in p["holdings"] if h["symbol"] == "AAPL")
    assert aapl["quantity"] == 6
    assert aapl["average_cost"] == pytest.approx(100.1)
    assert aapl["realized_pl"] == pytest.approx(4 * (120 - 100.1) - 1)
    assert aapl["market_value"] == pytest.approx(6 * aapl["price"])
    assert sum(h["allocation_pct"] for h in p["holdings"]) == pytest.approx(100)
    perf = client.get(f"/api/v1/portfolios/{pid}/performance").json()
    assert perf["series"] and "time_weighted_return_pct" in perf["metrics"] and perf["metrics"]["benchmark"] == "SPY"


def test_screener_and_csv(client, loaded):
    pres = client.get("/api/v1/screener/presets").json()
    assert "long_term_leaders" in pres["presets"]
    res = client.post("/api/v1/screener/run", json={"filters": [{"field": "price_above_sma200", "op": "is_true"}], "sort_by": "score"}).json()
    assert res["universe_size"] >= 4
    for r in res["results"]:
        assert r["price"] > r["sma_200"]
    # missing fundamentals never pass numeric filters
    res2 = client.post("/api/v1/screener/run", json={"filters": [{"field": "revenue_growth_yoy", "op": "gt", "value": 0}]}).json()
    assert res2["count"] == 0
    csv = client.post("/api/v1/screener/export", json={"filters": []})
    assert csv.headers["content-type"].startswith("text/csv") and csv.text.startswith("rank,symbol")
    assert client.post("/api/v1/screener/run", json={"filters": [{"field": "nope", "op": "gt", "value": 1}]}).status_code == 422


def test_backtest(client, loaded):
    r = client.post("/api/v1/backtest/run", json={"symbol": "AAPL", "strategy": {"require_macd_bull_cross": False, "rsi_min": 30, "rsi_max": 70}})
    assert r.status_code == 200, r.text
    b = r.json()
    for k in ("total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe_ratio", "sortino_ratio", "win_rate_pct", "number_of_trades", "average_holding_days"):
        assert k in b["metrics"]
    assert "SPY buy & hold" in b["benchmarks"] and "QQQ buy & hold" in b["benchmarks"]
    assert b["equity_curve"] and b["assumptions"]


def test_signals_center_and_status(client, loaded):
    sigs = client.get("/api/v1/signals", params={"primary_only": True}).json()
    assert sigs and all(s["is_primary"] for s in sigs)
    assert all(s["reasons"] and s["provider"] for s in sigs)
    summary = client.get("/api/v1/signals/summary").json()
    assert "ACCUMULATION_WATCH" in summary
    st = client.get("/api/v1/system/status").json()
    assert st["freshness"]["FRESH"] >= 4
    assert any(p["provider"] == "yahoo" for p in st["providers"])
    ov = client.get("/api/v1/market/overview").json()
    assert ov["market"]["status"] in ("OPEN", "CLOSED", "PRE_MARKET", "AFTER_HOURS")


def test_profile_weights(client, loaded):
    r = client.put("/api/v1/auth/me", json={"score_weights": {"trend_strength": 50, "momentum": 50, "fundamental_quality": 0, "financial_health": 0, "relative_performance": 0}})
    assert r.json()["score_weights"]["trend_strength"] == 50
    d = client.get("/api/v1/stocks/AAPL").json()
    assert d["personal_score"]["weights"]["momentum"] == 50
    assert client.put("/api/v1/auth/me", json={"score_weights": {"bogus": 1}}).status_code == 422


def test_rate_limit(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 5)
    codes = [client.get("/api/v1/auth/me").status_code for _ in range(8)]
    assert 429 in codes
