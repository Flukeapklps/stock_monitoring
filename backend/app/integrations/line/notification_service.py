"""Event-driven LINE notification service.

enqueue -> (preferences, follow status, dedupe, cooldown, max frequency, quiet hours)
        -> line_notification_history row (QUEUED)
dispatch -> SENDING -> push (X-Line-Retry-Key) -> ACCEPTED | QUEUED (retry w/ backoff) | FAILED | UNKNOWN

ACCEPTED means the LINE API accepted the request; it does not prove the message
was displayed on the user's device.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.line.client import LineApiError, LineClient
from app.integrations.line.quota_monitor import QuotaMonitor
from app.integrations.line.retry_handler import next_attempt_at
from app.integrations.line.validator import FlexValidationError, validate_messages
from app.models import (
    LineNotificationHistory,
    LineNotificationPreference,
    LineUser,
    NotificationHistory,
    Portfolio,
    PortfolioHolding,
    Stock,
    User,
    Watchlist,
    WatchlistItem,
)

log = logging.getLogger(__name__)

# notification_type -> default enabled
DEFAULT_PREFERENCES: dict[str, bool] = {
    "DAILY_SUMMARY": True,
    "WEEKLY_SUMMARY": True,
    "MONTHLY_SUMMARY": True,
    "STRONG_BULLISH": True,
    "ACCUMULATION": True,
    "TREND_WEAKENING": True,
    "PRICE_ALERT": False,
    "FUNDAMENTAL": True,
    "PORTFOLIO": True,
}
NOTIFICATION_LABELS = {
    "DAILY_SUMMARY": "Daily report",
    "WEEKLY_SUMMARY": "Weekly report",
    "MONTHLY_SUMMARY": "Monthly report",
    "STRONG_BULLISH": "Strong bullish alerts",
    "ACCUMULATION": "Accumulation alerts",
    "TREND_WEAKENING": "Trend weakening alerts",
    "PRICE_ALERT": "Price alerts",
    "FUNDAMENTAL": "Fundamental alerts",
    "PORTFOLIO": "Portfolio alerts",
    "TEST": "Test",
}
COMMAND_ALIASES = {
    "daily": "DAILY_SUMMARY",
    "weekly": "WEEKLY_SUMMARY",
    "monthly": "MONTHLY_SUMMARY",
    "bullish": "STRONG_BULLISH",
    "accumulation": "ACCUMULATION",
    "weakening": "TREND_WEAKENING",
    "price": "PRICE_ALERT",
    "fundamental": "FUNDAMENTAL",
    "portfolio": "PORTFOLIO",
}
GENERAL = "GENERAL"
REPORT_TYPES = {"DAILY_SUMMARY", "WEEKLY_SUMMARY", "MONTHLY_SUMMARY", "TEST"}


def default_general() -> dict:
    s = get_settings()
    return {
        "enabled": s.line_default_notification_enabled,
        "min_score": 0,
        "rsi_low": 40,
        "rsi_high": 50,
        "cooldown_minutes": s.line_alert_cooldown_minutes,
        "max_per_day": 20,
        "quiet_hours": None,  # {"start": "22:00", "end": "07:00"}
        "timezone": "Asia/Bangkok",
        "symbols": [],  # extra symbols to follow besides watchlists/portfolios
        "timeframes": ["DAILY", "WEEKLY"],
        "daily_report_time": s.line_daily_report_time,
        "weekly_report_time": s.line_weekly_report_time,
    }


# --- users / preferences -----------------------------------------------------------
def get_or_create_line_user(db: Session, line_user_id: str, display_name: str | None = None) -> LineUser:
    lu = db.scalar(select(LineUser).where(LineUser.line_user_id == line_user_id))
    if lu is None:
        lu = LineUser(line_user_id=line_user_id, display_name=display_name, is_following=True, followed_at=datetime.now(UTC))
        db.add(lu)
        db.flush()
    elif display_name and not lu.display_name:
        lu.display_name = display_name
    if lu.user_id is None:
        shadow = User(role="line", display_name=display_name or f"LINE {line_user_id[-6:]}", is_active=True)
        db.add(shadow)
        db.flush()
        lu.user_id = shadow.id
    ensure_preferences(db, lu)
    return lu


def ensure_preferences(db: Session, lu: LineUser) -> None:
    existing = {p.notification_type for p in db.scalars(select(LineNotificationPreference).where(LineNotificationPreference.user_id == lu.id))}
    for t, enabled in DEFAULT_PREFERENCES.items():
        if t not in existing:
            db.add(LineNotificationPreference(user_id=lu.id, notification_type=t, enabled=enabled, settings_json={}))
    if GENERAL not in existing:
        db.add(LineNotificationPreference(user_id=lu.id, notification_type=GENERAL, enabled=True, settings_json=default_general()))
    db.flush()


def get_preferences(db: Session, lu: LineUser) -> tuple[dict[str, dict], dict]:
    ensure_preferences(db, lu)
    rows = db.scalars(select(LineNotificationPreference).where(LineNotificationPreference.user_id == lu.id)).all()
    prefs, general = {}, default_general()
    for r in rows:
        if r.notification_type == GENERAL:
            general = {**general, **(r.settings_json or {}), "enabled": r.enabled}
        else:
            prefs[r.notification_type] = {"enabled": r.enabled, "settings": r.settings_json or {}}
    ordered = {k: prefs[k] for k in DEFAULT_PREFERENCES if k in prefs}
    return ordered, general


def set_preference(db: Session, lu: LineUser, notification_type: str, enabled: bool | None = None, settings: dict | None = None) -> None:
    ensure_preferences(db, lu)
    row = db.scalar(select(LineNotificationPreference).where(LineNotificationPreference.user_id == lu.id, LineNotificationPreference.notification_type == notification_type))
    if row is None:
        raise ValueError(f"unknown notification type {notification_type}")
    if enabled is not None:
        row.enabled = enabled
    if settings is not None:
        row.settings_json = {**(row.settings_json or {}), **settings}
    db.flush()


def bootstrap_admin_recipient(db: Session) -> LineUser | None:
    """Register LINE_USER_ID (if configured) and link it to the admin web user."""
    s = get_settings()
    if not s.line_user_id:
        return None
    lu = db.scalar(select(LineUser).where(LineUser.line_user_id == s.line_user_id))
    if lu is None:
        lu = LineUser(line_user_id=s.line_user_id, display_name="Admin (LINE_USER_ID)", is_following=True, followed_at=datetime.now(UTC))
        db.add(lu)
        db.flush()
    if lu.user_id is None and s.admin_email:
        admin = db.scalar(select(User).where(User.email == s.admin_email.lower()))
        if admin:
            lu.user_id = admin.id
    ensure_preferences(db, lu)
    return lu


def followed_symbols(db: Session, lu: LineUser) -> set[str]:
    _, general = get_preferences(db, lu)
    syms = {s.upper() for s in general.get("symbols") or []}
    if lu.user_id:
        q = select(Stock.symbol).join(WatchlistItem, WatchlistItem.stock_id == Stock.id).join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id).where(Watchlist.user_id == lu.user_id)
        syms |= set(db.scalars(q))
        q2 = select(Stock.symbol).join(PortfolioHolding, PortfolioHolding.stock_id == Stock.id).join(Portfolio, Portfolio.id == PortfolioHolding.portfolio_id).where(Portfolio.user_id == lu.user_id, PortfolioHolding.quantity > 0)
        syms |= set(db.scalars(q2))
    return syms


def recipients_for(db: Session, notification_type: str, symbol: str | None = None) -> list[LineUser]:
    users = db.scalars(select(LineUser).where(LineUser.is_following.is_(True))).all()
    out = []
    for lu in users:
        prefs, general = get_preferences(db, lu)
        if not general.get("enabled", True):
            continue
        p = prefs.get(notification_type)
        if notification_type != "TEST" and (p is None or not p["enabled"]):
            continue
        if symbol and symbol.upper() not in followed_symbols(db, lu):
            continue
        out.append(lu)
    return out


# --- enqueue ----------------------------------------------------------------------------
def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def quiet_until(general: dict, now: datetime) -> datetime | None:
    """If `now` falls inside the user's quiet hours, return when they end (UTC)."""
    qh = general.get("quiet_hours")
    if not qh or not qh.get("start") or not qh.get("end"):
        return None
    try:
        tz = ZoneInfo(general.get("timezone") or "UTC")
        start, end = _parse_hhmm(qh["start"]), _parse_hhmm(qh["end"])
    except Exception:
        return None
    local = now.astimezone(tz)
    t = local.time()
    inside = (start <= t < end) if start < end else (t >= start or t < end)
    if not inside:
        return None
    end_dt = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= local:
        end_dt += timedelta(days=1)
    return end_dt.astimezone(UTC)


def enqueue(
    db: Session,
    lu: LineUser,
    notification_type: str,
    messages: list[dict],
    *,
    dedupe_key: str,
    title: str,
    stock_symbol: str | None = None,
    summary: str | None = None,
    context: dict | None = None,
    cooldown_minutes: int | None = None,
    now: datetime | None = None,
    respect_preferences: bool = True,
) -> LineNotificationHistory | None:
    """Queue a notification for one LINE user. Returns None when suppressed."""
    now = now or datetime.now(UTC)
    if not lu.is_following:
        return None
    prefs, general = get_preferences(db, lu)
    if respect_preferences and notification_type not in ("TEST",):
        if not general.get("enabled", True) or not prefs.get(notification_type, {}).get("enabled", False):
            return None
    errors = validate_messages(messages)
    if errors:
        raise FlexValidationError(errors)
    recipient_key = f"line:{lu.id}"
    # dedupe (same alert never twice)
    exists = db.scalar(select(NotificationHistory.id).where(NotificationHistory.recipient_key == recipient_key, NotificationHistory.dedupe_key == dedupe_key))
    if exists:
        return None
    # cooldown per (recipient, category, symbol)
    cd = cooldown_minutes if cooldown_minutes is not None else int(general.get("cooldown_minutes") or get_settings().line_alert_cooldown_minutes)
    if cd and notification_type not in REPORT_TYPES:
        recent = db.scalar(
            select(NotificationHistory.id).where(
                NotificationHistory.recipient_key == recipient_key,
                NotificationHistory.category == notification_type,
                NotificationHistory.stock_symbol == stock_symbol,
                NotificationHistory.created_at >= now - timedelta(minutes=cd),
            )
        )
        if recent:
            return None
    nh = NotificationHistory(recipient_key=recipient_key, channel="LINE", category=notification_type, stock_symbol=stock_symbol, dedupe_key=dedupe_key, title=title[:200], summary=summary, context=context, created_at=now)
    db.add(nh)
    db.flush()
    status, err, next_at = "QUEUED", None, now
    # max frequency (reports and tests don't count against the alert budget)
    if notification_type not in REPORT_TYPES:
        sent_24h = db.scalar(
            select(func.count()).select_from(LineNotificationHistory).where(
                LineNotificationHistory.user_id == lu.id,
                LineNotificationHistory.created_at >= now - timedelta(hours=24),
                LineNotificationHistory.status.in_(("QUEUED", "SENDING", "ACCEPTED", "UNKNOWN")),
                LineNotificationHistory.notification_type.notin_(tuple(REPORT_TYPES)),
            )
        )
        if sent_24h >= int(general.get("max_per_day") or 20):
            status, err = "SKIPPED", "max notifications per 24h reached"
    if status == "QUEUED" and notification_type != "TEST":
        q = quiet_until(general, now)
        if q:
            next_at = q
    h = LineNotificationHistory(
        user_id=lu.id,
        notification_id=nh.id,
        stock_symbol=stock_symbol,
        notification_type=notification_type,
        message_payload=messages,
        status=status,
        attempt_count=0,
        retry_key=str(uuid.uuid4()),
        error_message=err,
        next_attempt_at=next_at,
        created_at=now,
    )
    db.add(h)
    db.flush()
    return h


def enqueue_for_recipients(db: Session, notification_type: str, symbol: str | None, build, *, dedupe_key: str, title: str, **kw) -> list[LineNotificationHistory]:
    """`build(lu)` returns messages for that recipient (allows per-user timezone)."""
    out = []
    for lu in recipients_for(db, notification_type, symbol):
        msgs = build(lu)
        h = enqueue(db, lu, notification_type, msgs, dedupe_key=dedupe_key, title=title, stock_symbol=symbol, **kw)
        if h:
            out.append(h)
    return out


# --- dispatch --------------------------------------------------------------------------
def dispatch_pending(db: Session, client: LineClient | None = None, *, limit: int = 50, now: datetime | None = None) -> dict:
    """Send queued notifications (safe for concurrent workers: SKIP LOCKED)."""
    s = get_settings()
    now = now or datetime.now(UTC)
    client = client or LineClient()
    stats = {"accepted": 0, "failed": 0, "retry": 0, "unknown": 0, "skipped": 0}
    if not s.line_enabled:
        return {**stats, "note": "LINE disabled"}
    rows = db.scalars(
        select(LineNotificationHistory)
        .where(LineNotificationHistory.status == "QUEUED", LineNotificationHistory.next_attempt_at <= now)
        .order_by(LineNotificationHistory.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    quota = QuotaMonitor(client)
    for h in rows:
        lu = db.get(LineUser, h.user_id) if h.user_id else None
        if lu is None or not lu.is_following:
            h.status, h.error_message = "SKIPPED", "recipient missing or not following (unfollowed/blocked)"
            stats["skipped"] += 1
            continue
        if not client.configured:
            h.status, h.error_message = "FAILED", "LINE_CHANNEL_ACCESS_TOKEN not configured"
            stats["failed"] += 1
            continue
        if not quota.can_send(db):
            h.status, h.error_message = "FAILED", "monthly message quota exhausted"
            stats["failed"] += 1
            continue
        h.status = "SENDING"
        h.attempt_count += 1
        db.flush()
        try:
            resp = client.push(lu.line_user_id, h.message_payload, retry_key=h.retry_key)
            h.status = "ACCEPTED"
            h.http_status = resp.status_code
            h.line_request_id = resp.accepted_request_id or resp.request_id
            h.api_response = resp.body if isinstance(resp.body, dict) else {"body": resp.body}
            h.sent_at = datetime.now(UTC)
            h.error_message = None
            quota.record_sent(1)
            stats["accepted"] += 1
        except LineApiError as exc:
            h.http_status = exc.status_code
            h.api_response = exc.body if isinstance(exc.body, dict) else None
            h.line_request_id = exc.request_id
            h.error_message = str(exc)[:500]
            if exc.kind in ("RETRYABLE", "UNKNOWN") and h.attempt_count < s.line_max_retry_attempts:
                h.status = "QUEUED"
                h.next_attempt_at = next_attempt_at(h.attempt_count, now)
                stats["retry"] += 1
            elif exc.kind == "UNKNOWN":
                h.status = "UNKNOWN"  # timed out on the final attempt: may or may not have been accepted
                stats["unknown"] += 1
            else:
                h.status = "FAILED"
                stats["failed"] += 1
                if exc.status_code == 400 and "user" in str(exc).lower():
                    log.warning("LINE rejected recipient for line_user %s", lu.id)
    db.commit()
    return stats


def reset_stuck_sending(db: Session, older_than_minutes: int = 10) -> int:
    """A crash between SENDING and the result leaves rows in SENDING; requeue them
    (the retry key keeps the resend idempotent on LINE's side)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    res = db.execute(
        update(LineNotificationHistory)
        .where(LineNotificationHistory.status == "SENDING", LineNotificationHistory.created_at < cutoff)
        .values(status="QUEUED", next_attempt_at=datetime.now(UTC))
        .returning(LineNotificationHistory.id)
    )
    return len(res.all())
