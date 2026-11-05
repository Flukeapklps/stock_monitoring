"""LINE integration tests. All LINE API calls are mocked with respx."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.analysis.service import analyze_stocks
from app.core.cache import reset_backend
from app.core.config import get_settings
from app.core.security import ensure_admin_user
from app.integrations.line import flex_templates as ft
from app.integrations.line import message_builder as mb
from app.integrations.line.client import LineApiError, LineClient
from app.integrations.line.command_handler import handle, parse_command
from app.integrations.line.notification_service import dispatch_pending, enqueue, get_or_create_line_user, quiet_until, set_preference
from app.integrations.line.retry_handler import backoff_delay, classify_status
from app.integrations.line.signature import compute_signature, verify_signature
from app.integrations.line.validator import validate_messages
from app.models import LineNotificationHistory, LineUser, LineWebhookEvent, NotificationHistory, Stock
from app.pipeline.ingestion import update_symbols

API = "https://api.line.me"
SECRET = "test-channel-secret"


def _event(etype, uid="U" + "a" * 32, eid="01H000000000000000000000AA", **extra):
    ev = {"type": etype, "mode": "active", "timestamp": 1730000000000, "source": {"type": "user", "userId": uid}, "webhookEventId": eid, "deliveryContext": {"isRedelivery": False}}
    if etype in ("follow", "message", "postback", "join"):
        ev["replyToken"] = "r" * 32
    ev.update(extra)
    return ev


def _post(c, events, secret=SECRET):
    body = json.dumps({"destination": "Uxxx", "events": events}).encode()
    return c.post("/api/v1/line/webhook", content=body, headers={"x-line-signature": compute_signature(body, secret), "content-type": "application/json"})


@pytest.fixture()
def app_client(db, fake_provider):
    from app.main import create_app

    ensure_admin_user(db)
    db.commit()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def loaded(db, fake_provider):
    update_symbols(db, ["AAPL", "SPY", "^GSPC"], years=2)
    analyze_stocks(db, list(db.scalars(select(Stock))))
    db.commit()


# --- signature -----------------------------------------------------------------------------------
def test_signature_valid_and_invalid():
    body = b'{"events":[]}'
    sig = compute_signature(body, SECRET)
    assert verify_signature(body, sig, SECRET)
    assert not verify_signature(body + b" ", sig, SECRET)  # raw body must match exactly
    assert not verify_signature(body, sig, "other-secret")
    assert not verify_signature(body, None, SECRET)
    assert not verify_signature(body, "garbage", SECRET)


def test_webhook_rejects_invalid_signature(app_client):
    body = b'{"events":[]}'
    r = app_client.post("/api/v1/line/webhook", content=body, headers={"x-line-signature": "invalid"})
    assert r.status_code == 401
    assert app_client.post("/api/v1/line/webhook", content=body).status_code == 401


def test_webhook_verify_empty_events(app_client):
    assert _post(app_client, []).status_code == 200


@respx.mock
def test_follow_registers_user_and_replies(app_client, db):
    respx.get(f"{API}/v2/bot/profile/U{'a' * 32}").mock(return_value=httpx.Response(200, json={"displayName": "Alice"}))
    reply = respx.post(f"{API}/v2/bot/message/reply").mock(return_value=httpx.Response(200, json={}))
    r = _post(app_client, [_event("follow")])
    assert r.status_code == 200 and r.json()["new"] == 1
    lu = db.scalar(select(LineUser))
    assert lu.is_following and lu.display_name == "Alice" and lu.user_id is not None
    assert reply.called
    sent = json.loads(reply.calls.last.request.content)
    assert "Welcome" in sent["messages"][0]["text"]
    ev = db.scalar(select(LineWebhookEvent))
    assert ev.processed


@respx.mock
def test_unfollow_stops_notifications(app_client, db):
    respx.post(f"{API}/v2/bot/message/reply").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=r".*/profile/.*").mock(return_value=httpx.Response(200, json={}))
    _post(app_client, [_event("follow")])
    _post(app_client, [_event("unfollow", eid="01H000000000000000000000BB")])
    db.expire_all()
    lu = db.scalar(select(LineUser))
    assert lu.is_following is False and lu.unfollowed_at is not None
    msgs = [mb.text_message("hi")]
    assert enqueue(db, lu, "TEST", msgs, dedupe_key="x", title="t", respect_preferences=False) is None


@respx.mock
def test_duplicate_webhook_event_processed_once(app_client, db):
    reply = respx.post(f"{API}/v2/bot/message/reply").mock(return_value=httpx.Response(200, json={}))
    ev = _event("message", message={"type": "text", "id": "1", "text": "/help"})
    _post(app_client, [ev])
    r2 = _post(app_client, [ev])
    assert r2.json()["new"] == 0
    assert db.scalar(select(func.count()).select_from(LineWebhookEvent)) == 1
    assert reply.call_count == 1


# --- commands ------------------------------------------------------------------------------------
def test_command_parsing():
    assert parse_command("/stock aapl").args == ["aapl"]
    assert parse_command("/STOCK AAPL").name == "stock"
    assert parse_command("SCORE msft").name == "score"
    assert parse_command("／help").name == "help"
    assert parse_command("hello there") is None
    assert parse_command("") is None


def test_commands_end_to_end(db, loaded):
    lu = get_or_create_line_user(db, "U" + "b" * 32, "Bob")
    db.commit()
    assert "commands" in handle(db, lu, "/help")[0]["text"]
    assert handle(db, lu, "/watchlist")[0]["text"].startswith("Your watchlist is empty")
    out = handle(db, lu, "/watch aapl")
    assert "AAPL" in out[0]["text"]
    wl = handle(db, lu, "/watchlist")
    assert wl[0]["type"] == "flex" and not validate_messages(wl)
    for cmd in ("/stock AAPL", "/signal aapl", "/score AAPL", "/market", "/top", "/settings", "/portfolio", "/test"):
        msgs = handle(db, lu, cmd)
        assert msgs and not validate_messages(msgs), cmd
    assert "not tracked" in handle(db, lu, "/stock MSFT")[0]["text"]
    assert "⚠️" in handle(db, lu, "/stock bad;;")[0]["text"]
    assert "unknown symbol" in handle(db, lu, "/watch ZZZZ")[0]["text"].lower()
    assert "Usage" in handle(db, lu, "/stock")[0]["text"]
    assert "turned off" in handle(db, lu, "/notify accumulation off")[0]["text"]
    assert "Removed" in handle(db, lu, "/unwatch AAPL")[0]["text"]


def test_link_command(db, loaded):
    from app.models import User

    admin = ensure_admin_user(db)
    admin.line_link_code = "ABCD1234"
    lu = get_or_create_line_user(db, "U" + "c" * 32)
    db.commit()
    handle(db, lu, "/watch AAPL")
    out = handle(db, lu, "/link abcd1234")
    assert "Linked" in out[0]["text"]
    db.refresh(lu)
    assert lu.user_id == admin.id
    assert db.get(User, admin.id).line_link_code is None


# --- flex ----------------------------------------------------------------------------------------
def test_flex_alert_card_valid(db, loaded):
    st = db.scalar(select(Stock).where(Stock.symbol == "AAPL"))
    msgs = mb.stock_card(db, st, signal_override="ACCUMULATION_WATCH")
    assert validate_messages(msgs) == []
    m = msgs[0]
    assert m["type"] == "flex" and "AAPL" in m["altText"]
    dump = json.dumps(m)
    assert "ACCUMULATION WATCH" in dump and "Investment Score" in dump and "END_OF_DAY" in dump


def test_flex_validation_catches_errors():
    bad_color = ft.flex("x", ft.simple_card("t", ["a"], color="green"))
    assert any("invalid color" in e for e in validate_messages([bad_color]))
    assert validate_messages([{"type": "flex", "altText": "", "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}}])
    bad_btn = {"type": "flex", "altText": "x", "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "x", "uri": "javascript:alert(1)"}}]}}}
    assert any("uri" in e for e in validate_messages([bad_btn]))
    assert validate_messages([{"type": "text", "text": "x" * 5001}])
    assert validate_messages([{"type": "text", "text": "a"}] * 6)
    baseline_bad = {"type": "flex", "altText": "x", "contents": {"type": "bubble", "body": {"type": "box", "layout": "baseline", "contents": [{"type": "button", "action": {"type": "message", "label": "a", "text": "b"}}]}}}
    assert validate_messages([baseline_bad])


def test_buttons_only_https(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_base_url", "http://localhost:3000")
    assert ft.buttons([("View", "http://localhost:3000/x")]) is None
    b = ft.buttons([("View", "https://stocks.example.com/x")])
    assert b["contents"][0]["action"]["uri"].startswith("https://")


# --- client / retry / dispatch ---------------------------------------------------------------------
def test_retry_classification():
    assert classify_status(500) == "RETRYABLE"
    assert classify_status(429, "Too many requests") == "RETRYABLE"
    assert classify_status(429, "You have reached your monthly limit.") == "QUOTA"
    assert classify_status(400) == "PERMANENT" and classify_status(401) == "PERMANENT"
    assert classify_status(None) == "UNKNOWN"
    assert [backoff_delay(i) for i in (1, 2, 3)] == [30, 60, 120]


@respx.mock
def test_push_sends_retry_key_and_token():
    route = respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(200, json={}, headers={"x-line-request-id": "req-1"}))
    c = LineClient(access_token="tok")
    resp = c.push("Uxyz", [mb.text_message("hi")], retry_key="11111111-1111-1111-1111-111111111111")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer tok"
    assert req.headers["x-line-retry-key"] == "11111111-1111-1111-1111-111111111111"
    assert resp.request_id == "req-1"


@respx.mock
def test_reply_message():
    route = respx.post(f"{API}/v2/bot/message/reply").mock(return_value=httpx.Response(200, json={}))
    LineClient(access_token="tok").reply("tokenX", [mb.text_message("hi")])
    assert json.loads(route.calls.last.request.content)["replyToken"] == "tokenX"
    assert "x-line-retry-key" not in route.calls.last.request.headers  # reply is never retried


@respx.mock
def test_invalid_token_and_recipient():
    respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(401, json={"message": "Authentication failed"}))
    with pytest.raises(LineApiError) as e:
        LineClient(access_token="bad").push("U1", [mb.text_message("x")])
    assert e.value.kind == "PERMANENT" and e.value.status_code == 401
    with pytest.raises(LineApiError) as e2:
        LineClient(access_token="").push("U1", [mb.text_message("x")])
    assert e2.value.kind == "NOT_CONFIGURED"


def _queued(db, lu, key="k1", ntype="TEST"):
    h = enqueue(db, lu, ntype, [mb.text_message("hello")], dedupe_key=key, title="t", respect_preferences=False)
    db.commit()
    return h


def _quota_ok():
    respx.get(f"{API}/v2/bot/message/quota").mock(return_value=httpx.Response(200, json={"type": "limited", "value": 200}))
    respx.get(f"{API}/v2/bot/message/quota/consumption").mock(return_value=httpx.Response(200, json={"totalUsage": 10}))


@respx.mock
def test_dispatch_success(db):
    _quota_ok()
    respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(200, json={"sentMessages": [{"id": "1"}]}, headers={"x-line-request-id": "abc"}))
    lu = get_or_create_line_user(db, "U" + "d" * 32)
    h = _queued(db, lu)
    stats = dispatch_pending(db, LineClient(access_token="tok"))
    db.refresh(h)
    assert stats["accepted"] == 1 and h.status == "ACCEPTED" and h.line_request_id == "abc" and h.sent_at


@respx.mock
def test_dispatch_retry_then_success_same_retry_key(db):
    _quota_ok()
    route = respx.post(f"{API}/v2/bot/message/push").mock(side_effect=[httpx.Response(500, json={"message": "boom"}), httpx.Response(200, json={})])
    lu = get_or_create_line_user(db, "U" + "e" * 32)
    h = _queued(db, lu)
    c = LineClient(access_token="tok")
    s1 = dispatch_pending(db, c)
    db.refresh(h)
    assert s1["retry"] == 1 and h.status == "QUEUED" and h.attempt_count == 1 and h.next_attempt_at > datetime.now(UTC)
    s2 = dispatch_pending(db, c, now=datetime.now(UTC) + timedelta(minutes=5))
    db.refresh(h)
    assert s2["accepted"] == 1 and h.status == "ACCEPTED" and h.attempt_count == 2
    keys = {call.request.headers["x-line-retry-key"] for call in route.calls}
    assert keys == {h.retry_key}


@respx.mock
def test_dispatch_duplicate_retry_key_409_is_success(db):
    _quota_ok()
    respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(409, json={"message": "conflict"}, headers={"x-line-accepted-request-id": "orig-req"}))
    lu = get_or_create_line_user(db, "U" + "f" * 32)
    h = _queued(db, lu)
    dispatch_pending(db, LineClient(access_token="tok"))
    db.refresh(h)
    assert h.status == "ACCEPTED" and h.line_request_id == "orig-req"


@respx.mock
def test_dispatch_permanent_failure_not_retried(db):
    _quota_ok()
    route = respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(400, json={"message": "The property, 'to', in the request body is invalid"}))
    lu = get_or_create_line_user(db, "U" + "g" * 32)
    h = _queued(db, lu)
    c = LineClient(access_token="tok")
    dispatch_pending(db, c)
    dispatch_pending(db, c, now=datetime.now(UTC) + timedelta(hours=1))
    db.refresh(h)
    assert h.status == "FAILED" and route.call_count == 1 and "invalid" in h.error_message


@respx.mock
def test_dispatch_gives_up_after_max_attempts(db, monkeypatch):
    _quota_ok()
    monkeypatch.setattr(get_settings(), "line_max_retry_attempts", 2)
    respx.post(f"{API}/v2/bot/message/push").mock(side_effect=httpx.ReadTimeout("timeout"))
    lu = get_or_create_line_user(db, "U" + "h" * 32)
    h = _queued(db, lu)
    c = LineClient(access_token="tok")
    dispatch_pending(db, c)
    dispatch_pending(db, c, now=datetime.now(UTC) + timedelta(hours=1))
    db.refresh(h)
    assert h.status == "UNKNOWN" and h.attempt_count == 2  # timeout: delivery status unknown


@respx.mock
def test_quota_exhausted_blocks_send(db):
    reset_backend(memory=True)
    respx.get(f"{API}/v2/bot/message/quota").mock(return_value=httpx.Response(200, json={"type": "limited", "value": 200}))
    respx.get(f"{API}/v2/bot/message/quota/consumption").mock(return_value=httpx.Response(200, json={"totalUsage": 200}))
    push = respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(200, json={}))
    lu = get_or_create_line_user(db, "U" + "i" * 32)
    h = _queued(db, lu)
    dispatch_pending(db, LineClient(access_token="tok"))
    db.refresh(h)
    assert h.status == "FAILED" and "quota" in h.error_message and not push.called


def test_missing_recipient_and_unconfigured(db):
    lu = get_or_create_line_user(db, "U" + "j" * 32)
    h = _queued(db, lu)
    lu.is_following = False
    db.commit()
    dispatch_pending(db, LineClient(access_token="tok"))
    db.refresh(h)
    assert h.status == "SKIPPED"
    lu.is_following = True
    h2 = _queued(db, lu, key="k2")
    dispatch_pending(db, LineClient(access_token=""))
    db.refresh(h2)
    assert h2.status == "FAILED" and "not configured" in h2.error_message


# --- preferences, cooldown, dedupe ---------------------------------------------------------------
def test_disabled_preferences_and_dedupe_and_cooldown(db):
    lu = get_or_create_line_user(db, "U" + "k" * 32)
    db.commit()
    msgs = [mb.text_message("alert")]
    # PRICE_ALERT disabled by default
    assert enqueue(db, lu, "PRICE_ALERT", msgs, dedupe_key="p1", title="t", stock_symbol="AAPL") is None
    a = enqueue(db, lu, "ACCUMULATION", msgs, dedupe_key="ACC:AAPL:2026-01-02", title="t", stock_symbol="AAPL")
    assert a is not None
    # duplicate alert
    assert enqueue(db, lu, "ACCUMULATION", msgs, dedupe_key="ACC:AAPL:2026-01-02", title="t", stock_symbol="AAPL") is None
    # cooldown: new date, same symbol/category, within 1440 min
    assert enqueue(db, lu, "ACCUMULATION", msgs, dedupe_key="ACC:AAPL:2026-01-05", title="t", stock_symbol="AAPL") is None
    # after cooldown
    later = datetime.now(UTC) + timedelta(days=2)
    assert enqueue(db, lu, "ACCUMULATION", msgs, dedupe_key="ACC:AAPL:2026-01-07", title="t", stock_symbol="AAPL", now=later) is not None
    set_preference(db, lu, "ACCUMULATION", enabled=False)
    assert enqueue(db, lu, "ACCUMULATION", msgs, dedupe_key="ACC:MSFT:x", title="t", stock_symbol="MSFT", now=later + timedelta(days=3)) is None
    assert db.scalar(select(func.count()).select_from(NotificationHistory)) == 2


def test_quiet_hours_defers(db):
    general = {"quiet_hours": {"start": "22:00", "end": "07:00"}, "timezone": "Asia/Bangkok"}
    inside = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)  # 23:00 Bangkok
    until = quiet_until(general, inside)
    assert until == datetime(2026, 1, 6, 0, 0, tzinfo=UTC)  # 07:00 Bangkok
    assert quiet_until(general, datetime(2026, 1, 5, 6, 0, tzinfo=UTC)) is None  # 13:00 Bangkok


def test_max_frequency(db):
    lu = get_or_create_line_user(db, "U" + "l" * 32)
    set_preference(db, lu, "GENERAL", settings={"max_per_day": 2, "cooldown_minutes": 0})
    db.commit()
    res = [enqueue(db, lu, "ACCUMULATION", [mb.text_message("x")], dedupe_key=f"d{i}", title="t", stock_symbol=f"S{i}") for i in range(3)]
    assert [r.status for r in res] == ["QUEUED", "QUEUED", "SKIPPED"]


# --- alerts from analysis & reports --------------------------------------------------------------
def test_alerts_and_reports_pipeline(db, loaded):
    from app.analysis.service import AnalysisResult
    from app.integrations.line import alerts, reports

    lu = get_or_create_line_user(db, "U" + "m" * 32)
    db.commit()
    handle(db, lu, "/watch AAPL")
    st = db.scalar(select(Stock).where(Stock.symbol == "AAPL"))
    res = analyze_stocks(db, [st])[0]
    # Force a "new accumulation" transition for the test
    res.primary_signal, res.previous_primary_signal = "ACCUMULATION_WATCH", "BULLISH"
    res.daily = {**(res.daily or {}), "rsi_14": 45.0}
    from app.models import InvestmentSignal

    db.add(InvestmentSignal(stock_id=st.id, signal_date=res.trade_date, timeframe="DAILY", signal_type="ACCUMULATION_WATCH", is_primary=False, score=80, confidence=0.5, confirmation_status="CONFIRMED", indicator_snapshot={}, reasons=["RSI in 40-50 accumulation range (RSI 45)"], provider="fake", data_status="END_OF_DAY"))
    db.commit()
    n = alerts.alerts_from_analysis(db, [res])
    assert n == 1
    assert alerts.alerts_from_analysis(db, [res]) == 0  # deduplicated
    h = db.scalar(select(LineNotificationHistory).where(LineNotificationHistory.notification_type == "ACCUMULATION"))
    assert not validate_messages(h.message_payload)
    assert reports.send_reports(db, "daily") == 1
    assert reports.send_reports(db, "daily") == 0  # once per session
    assert reports.send_reports(db, "weekly") == 1
    assert reports.send_reports(db, "monthly") == 1
    for row in db.scalars(select(LineNotificationHistory)):
        assert validate_messages(row.message_payload) == [], row.notification_type
    assert isinstance(res, AnalysisResult)


def test_price_rule_crossing():
    from app.integrations.line.alerts import evaluate_price_rule

    assert evaluate_price_rule("PRICE_ABOVE", {"price": 100}, 99, 101)[0]
    assert not evaluate_price_rule("PRICE_ABOVE", {"price": 100}, 101, 102)[0]  # already above: no repeat
    assert evaluate_price_rule("RISK_THRESHOLD", {"price": 90}, 91, 89)[0]
    assert evaluate_price_rule("ACCUMULATION_ZONE", {"low": 95, "high": 100}, 102, 98)[0]


# --- admin endpoints -----------------------------------------------------------------------------
@respx.mock
def test_admin_test_endpoint(app_client, db, monkeypatch):
    _quota_ok()
    monkeypatch.setattr(get_settings(), "line_user_id", "U" + "n" * 32)
    get_or_create_line_user(db, "U" + "n" * 32)
    db.commit()
    respx.post(f"{API}/v2/bot/message/validate/push").mock(return_value=httpx.Response(200, json={}))
    push = respx.post(f"{API}/v2/bot/message/push").mock(return_value=httpx.Response(200, json={}))
    app_client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-123"})
    r = app_client.post("/api/v1/line/test", json={})
    assert r.status_code == 200, r.text
    assert r.json()["dispatch"]["accepted"] == 1 and push.called
    st = app_client.get("/api/v1/line/status").json()
    assert st["configured"] and st["access_token"].startswith("••••")  # token is masked
    assert get_settings().line_channel_access_token not in json.dumps(st)
    hist = app_client.get("/api/v1/line/history").json()
    assert hist["total"] == 1 and hist["items"][0]["status"] == "ACCEPTED"
    prefs = app_client.get("/api/v1/line/preferences", params={"line_user_id": "U" + "n" * 32}).json()
    assert any(p["type"] == "PRICE_ALERT" and p["enabled"] is False for p in prefs["preferences"])
    upd = app_client.put("/api/v1/line/preferences", json={"line_user_id": "U" + "n" * 32, "preferences": {"PRICE_ALERT": True}, "general": {"min_score": 70, "quiet_hours": {"start": "22:00", "end": "07:00"}}}).json()
    assert any(p["type"] == "PRICE_ALERT" and p["enabled"] for p in upd["preferences"]) and upd["general"]["min_score"] == 70
    assert app_client.put("/api/v1/line/preferences", json={"line_user_id": "U" + "n" * 32, "general": {"quiet_hours": {"start": "25:00", "end": "07:00"}}}).status_code == 422
