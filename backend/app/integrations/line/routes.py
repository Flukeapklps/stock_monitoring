"""LINE integration HTTP endpoints (/api/v1/line/*).

Only the webhook is public (and protected by X-Line-Signature). Every other
endpoint requires an authenticated user; sending endpoints require an admin.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import get_current_user, require_admin
from app.db.session import get_db
from app.integrations.line import flex_templates as ft
from app.integrations.line import message_builder as mb
from app.integrations.line import reports
from app.integrations.line.client import LineApiError, LineClient
from app.integrations.line.notification_service import (
    DEFAULT_PREFERENCES,
    GENERAL,
    NOTIFICATION_LABELS,
    dispatch_pending,
    enqueue,
    get_preferences,
    set_preference,
)
from app.integrations.line.quota_monitor import QuotaMonitor
from app.integrations.line.schemas import AlertRuleIn, AlertTestRequest, PreferenceUpdate, ReportRequest, SendRequest, TestRequest
from app.integrations.line.signature import verify_signature
from app.integrations.line.validator import FlexValidationError, validate_messages
from app.integrations.line.webhook import process_stored_events, store_events
from app.models import LineAlertRule, LineNotificationHistory, LineUser, LineWebhookEvent, Stock, User
from app.pipeline import repository as repo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/line", tags=["line"])


def client_dep() -> LineClient:
    return LineClient()


def mask(token: str | None) -> str | None:
    if not token:
        return None
    return f"••••{token[-4:]} ({len(token)} chars)" if len(token) > 12 else "••••"


# --- webhook ------------------------------------------------------------------------------
@router.post("/webhook", include_in_schema=True)
async def webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db), client: LineClient = Depends(client_dep)):
    s = get_settings()
    body = await request.body()  # raw bytes: signature is computed over the original body
    signature = request.headers.get("x-line-signature")
    if not verify_signature(body, signature, s.line_channel_secret):
        log.warning("rejected LINE webhook with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    ids = store_events(db, payload)
    if ids:
        background.add_task(process_stored_events, ids, client)
    # LINE "Verify" sends an empty events list; 200 is the expected response.
    return {"status": "ok", "received": len(payload.get("events", [])), "new": len(ids)}


# --- status / quota / history ---------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    s = get_settings()
    since = datetime.now(UTC) - timedelta(hours=24)
    last_event = db.scalar(select(LineWebhookEvent).order_by(LineWebhookEvent.received_at.desc()).limit(1))
    last_ok = db.scalar(select(LineNotificationHistory).where(LineNotificationHistory.status == "ACCEPTED").order_by(LineNotificationHistory.sent_at.desc()).limit(1))
    errors_24h = db.scalar(select(func.count()).select_from(LineNotificationHistory).where(LineNotificationHistory.status.in_(("FAILED", "UNKNOWN")), LineNotificationHistory.created_at >= since))
    webhook_errors = db.scalar(select(func.count()).select_from(LineWebhookEvent).where(LineWebhookEvent.error_message.is_not(None), LineWebhookEvent.received_at >= since))
    queued = db.scalar(select(func.count()).select_from(LineNotificationHistory).where(LineNotificationHistory.status == "QUEUED"))
    followers = db.scalar(select(func.count()).select_from(LineUser).where(LineUser.is_following.is_(True)))
    connected = s.line_enabled and s.line_configured
    return {
        "enabled": s.line_enabled,
        "configured": s.line_configured,
        "connection_status": "CONNECTED" if connected and errors_24h == 0 else ("DEGRADED" if connected else "DISCONNECTED"),
        "access_token": mask(s.line_channel_access_token),
        "channel_secret_configured": bool(s.line_channel_secret),
        "admin_recipient_configured": bool(s.line_user_id),
        "webhook_url": s.line_webhook_url,
        "webhook_status": "RECEIVING" if last_event and last_event.received_at >= datetime.now(UTC) - timedelta(days=7) else ("NO_EVENTS" if not last_event else "IDLE"),
        "last_webhook_event_at": last_event.received_at if last_event else None,
        "last_webhook_event_type": last_event.event_type if last_event else None,
        "last_successful_message_at": last_ok.sent_at if last_ok else None,
        "api_error_count_24h": errors_24h,
        "webhook_error_count_24h": webhook_errors,
        "queued": queued,
        "followers": followers,
        "daily_report_time": s.line_daily_report_time,
        "weekly_report_time": s.line_weekly_report_time,
        "cooldown_minutes": s.line_alert_cooldown_minutes,
        "max_retry_attempts": s.line_max_retry_attempts,
    }


@router.get("/quota")
def quota(refresh: bool = False, _user: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    if not client.configured:
        return {"ok": False, "error": "LINE not configured"}
    return QuotaMonitor(client).fetch(force=refresh)


def _preview(payload: list) -> str:
    for m in payload or []:
        if m.get("type") == "text":
            return m["text"][:160]
        if m.get("type") == "flex":
            return m.get("altText", "")[:160]
    return ""


@router.get("/history")
def history(
    limit: int = Query(50, le=500),
    offset: int = 0,
    status: str | None = None,
    symbol: str | None = None,
    notification_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(LineNotificationHistory, LineUser).outerjoin(LineUser, LineUser.id == LineNotificationHistory.user_id)
    if user.role != "admin":
        q = q.where(LineUser.user_id == user.id)
    if status:
        q = q.where(LineNotificationHistory.status == status.upper())
    if symbol:
        q = q.where(LineNotificationHistory.stock_symbol == symbol.upper())
    if notification_type:
        q = q.where(LineNotificationHistory.notification_type == notification_type.upper())
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(LineNotificationHistory.created_at.desc()).offset(offset).limit(limit)).all()
    items = [
        {
            "id": h.id,
            "line_user_id": lu.line_user_id if lu else None,
            "display_name": lu.display_name if lu else None,
            "stock_symbol": h.stock_symbol,
            "notification_type": h.notification_type,
            "status": h.status,
            "attempt_count": h.attempt_count,
            "http_status": h.http_status,
            "line_request_id": h.line_request_id,
            "error_message": h.error_message,
            "created_at": h.created_at,
            "sent_at": h.sent_at,
            "next_attempt_at": h.next_attempt_at,
            "preview": _preview(h.message_payload),
            "message_payload": h.message_payload,
            "api_response": h.api_response,
        }
        for h, lu in rows
    ]
    return {"total": total, "items": items}


@router.get("/users")
def users(db: Session = Depends(get_db), _user: User = Depends(require_admin)):
    rows = db.scalars(select(LineUser).order_by(LineUser.created_at.desc())).all()
    out = []
    for lu in rows:
        linked = db.get(User, lu.user_id) if lu.user_id else None
        out.append(
            {
                "id": lu.id,
                "line_user_id": lu.line_user_id,
                "display_name": lu.display_name,
                "is_following": lu.is_following,
                "linked_user": linked.email if linked and linked.role != "line" else None,
                "created_at": lu.created_at,
                "followed_at": lu.followed_at,
                "unfollowed_at": lu.unfollowed_at,
            }
        )
    return out


# --- preferences ----------------------------------------------------------------------------------
def _resolve_line_user(db: Session, user: User, line_user_id: str | None) -> LineUser:
    if line_user_id:
        lu = db.scalar(select(LineUser).where(LineUser.line_user_id == line_user_id))
        if not lu or (user.role != "admin" and lu.user_id != user.id):
            raise HTTPException(404, "LINE user not found")
        return lu
    lu = db.scalar(select(LineUser).where(LineUser.user_id == user.id).order_by(LineUser.id))
    if not lu and user.role == "admin" and get_settings().line_user_id:
        lu = db.scalar(select(LineUser).where(LineUser.line_user_id == get_settings().line_user_id))
    if not lu:
        raise HTTPException(404, "No LINE account linked. Generate a link code and send /link CODE to the bot.")
    return lu


@router.get("/preferences")
def get_prefs(line_user_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lu = _resolve_line_user(db, user, line_user_id)
    prefs, general = get_preferences(db, lu)
    db.commit()
    rules = db.scalars(select(LineAlertRule).where(LineAlertRule.user_id == lu.id)).all()
    return {
        "line_user_id": lu.line_user_id,
        "display_name": lu.display_name,
        "is_following": lu.is_following,
        "preferences": [{"type": k, "label": NOTIFICATION_LABELS.get(k, k), **v} for k, v in prefs.items()],
        "general": general,
        "alert_rules": [{"id": r.id, "stock_symbol": r.stock_symbol, "rule_type": r.rule_type, "conditions": r.conditions_json, "enabled": r.enabled, "cooldown_minutes": r.cooldown_minutes, "last_triggered_at": r.last_triggered_at} for r in rules],
    }


@router.put("/preferences")
def put_prefs(body: PreferenceUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lu = _resolve_line_user(db, user, body.line_user_id)
    for t, enabled in body.preferences.items():
        if t not in DEFAULT_PREFERENCES:
            raise HTTPException(422, f"unknown notification type {t}")
        set_preference(db, lu, t, enabled=enabled)
    if body.general is not None:
        enabled = body.general.pop("enabled", None)
        set_preference(db, lu, GENERAL, enabled=enabled, settings=body.general)
    db.commit()
    return get_prefs(lu.line_user_id, db, user)


@router.post("/alert-rules")
def create_rule(body: AlertRuleIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.integrations.line.alerts import PRICE_RULES, evaluate_price_rule

    lu = _resolve_line_user(db, user, body.line_user_id)
    if body.rule_type not in PRICE_RULES:
        raise HTTPException(422, f"rule_type must be one of {sorted(PRICE_RULES)}")
    try:
        evaluate_price_rule(body.rule_type, body.conditions, None, 1.0)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, "invalid conditions (PRICE_*: {price}; ACCUMULATION_ZONE: {low, high})") from None
    sym = repo.normalize_symbol(body.stock_symbol)
    if not repo.get_stock(db, sym):
        raise HTTPException(404, f"{sym} is not tracked")
    r = LineAlertRule(user_id=lu.id, stock_symbol=sym, rule_type=body.rule_type, conditions_json=body.conditions, enabled=body.enabled, cooldown_minutes=body.cooldown_minutes)
    db.add(r)
    db.commit()
    return {"id": r.id}


@router.delete("/alert-rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(LineAlertRule, rule_id)
    if not r:
        raise HTTPException(404, "not found")
    lu = db.get(LineUser, r.user_id)
    if user.role != "admin" and (not lu or lu.user_id != user.id):
        raise HTTPException(404, "not found")
    db.delete(r)
    db.commit()
    return {"deleted": rule_id}


@router.post("/link-code")
def link_code(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    code = secrets.token_hex(4).upper()
    user.line_link_code = code
    db.commit()
    return {"code": code, "instructions": f"Send '/link {code}' to the LINE Official Account."}


# --- sending (admin) -------------------------------------------------------------------------------
def _target_users(db: Session, line_user_id: str | None) -> list[LineUser]:
    s = get_settings()
    target = line_user_id or s.line_user_id
    if target:
        lu = db.scalar(select(LineUser).where(LineUser.line_user_id == target))
        if not lu:
            raise HTTPException(404, "Recipient is not registered (they must follow the Official Account first, or set LINE_USER_ID).")
        return [lu]
    raise HTTPException(422, "No recipient: pass line_user_id or configure LINE_USER_ID")


def _send_now(db: Session, client: LineClient) -> dict:
    return dispatch_pending(db, client)


@router.post("/test")
def test_message(body: TestRequest, db: Session = Depends(get_db), _admin: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    if not client.configured:
        raise HTTPException(503, "LINE_CHANNEL_ACCESS_TOKEN is not configured")
    lus = _target_users(db, body.line_user_id)
    if body.symbol:
        st = repo.get_stock(db, body.symbol)
        if not st:
            raise HTTPException(404, f"{body.symbol} is not tracked")
        msgs = mb.stock_card(db, st)
    else:
        card = ft.simple_card("✅ Test notification", ["LINE integration is working.", f"Sent {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"], color=ft.GREEN, footer_note="Stock Monitor test message")
        msgs = mb.validated([ft.flex("Stock Monitor test notification", card)])
    server_validation = None
    try:
        client.validate_push(msgs)
        server_validation = "OK"
    except LineApiError as exc:
        server_validation = f"{exc.status_code}: {str(exc)[:200]}"
        if exc.kind == "PERMANENT" and exc.status_code == 400:
            raise HTTPException(422, f"LINE rejected the message: {exc}") from None
    queued = [enqueue(db, lu, "TEST", msgs, dedupe_key=f"TEST:{secrets.token_hex(6)}", title="Test notification", respect_preferences=False) for lu in lus]
    db.commit()
    stats = _send_now(db, client)
    return {"queued": len([q for q in queued if q]), "dispatch": stats, "server_validation": server_validation}


@router.post("/connection-test")
def connection_test(_admin: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    if not client.configured:
        return {"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKEN is not configured"}
    try:
        info = client.bot_info()
        out = {"ok": True, "bot": {k: info.get(k) for k in ("displayName", "basicId", "premiumId", "chatMode", "markAsReadMode")}}
        try:
            out["webhook"] = client.webhook_endpoint()
        except LineApiError as exc:
            out["webhook_error"] = str(exc)[:200]
        return out
    except LineApiError as exc:
        return {"ok": False, "status_code": exc.status_code, "error": str(exc)[:300], "hint": "Invalid channel access token" if exc.status_code == 401 else None}


@router.post("/send")
def send(body: SendRequest, db: Session = Depends(get_db), _admin: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    if not body.text and not body.messages:
        raise HTTPException(422, "text or messages required")
    msgs = body.messages or [mb.text_message(body.text)]
    errors = validate_messages(msgs)
    if errors:
        raise HTTPException(422, {"validation_errors": errors})
    if body.line_user_ids:
        lus = list(db.scalars(select(LineUser).where(LineUser.line_user_id.in_(body.line_user_ids))))
    else:
        lus = list(db.scalars(select(LineUser).where(LineUser.is_following.is_(True))))
    if not lus:
        raise HTTPException(404, "no registered recipients")
    n = 0
    for lu in lus:
        if enqueue(db, lu, "TEST", msgs, dedupe_key=f"ADMIN:{secrets.token_hex(6)}", title="Admin message", respect_preferences=False):
            n += 1
    db.commit()
    return {"queued": n, "dispatch": _send_now(db, client)}


@router.post("/alerts/test")
def alert_test(body: AlertTestRequest, db: Session = Depends(get_db), _admin: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    st = repo.get_stock(db, body.symbol)
    if not st:
        raise HTTPException(404, f"{body.symbol} is not tracked")
    try:
        msgs = mb.stock_card(db, st, signal_override=body.signal)
    except FlexValidationError as exc:
        raise HTTPException(422, {"validation_errors": exc.errors}) from None
    lus = _target_users(db, body.line_user_id)
    n = sum(1 for lu in lus if enqueue(db, lu, "TEST", msgs, dedupe_key=f"ALERTTEST:{secrets.token_hex(6)}", title=f"Alert test {st.symbol}", stock_symbol=st.symbol, respect_preferences=False))
    db.commit()
    return {"queued": n, "dispatch": _send_now(db, client) if client.configured else {"note": "LINE not configured; message queued only"}, "preview": msgs}


@router.post("/reports/{kind}")
def report(kind: str, body: ReportRequest | None = None, db: Session = Depends(get_db), _admin: User = Depends(require_admin), client: LineClient = Depends(client_dep)):
    if kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(404, "unknown report")
    only = None
    if body and body.line_user_id:
        only = _target_users(db, body.line_user_id)[0]
    n = reports.send_reports(db, kind, only=only)
    db.commit()
    return {"queued": n, "dispatch": _send_now(db, client) if client.configured else {"note": "LINE not configured; queued only"}}


@router.get("/preview/{symbol}")
def preview(symbol: str, signal: str | None = None, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    """Return the Flex payload for a stock card (used by the dashboard preview)."""
    st = repo.get_stock(db, symbol)
    if not st:
        raise HTTPException(404, f"{symbol} is not tracked")
    return {"messages": mb.stock_card(db, st, signal_override=signal)}


__all__ = ["router", "Stock"]
