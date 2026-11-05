"""Webhook event storage and asynchronous processing.

The HTTP handler (routes.py) verifies the signature on the raw body, stores the
events (deduplicated by `webhookEventId`) and returns 200 immediately; events are
processed afterwards in a background task.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.integrations.line import message_builder as mb
from app.integrations.line.client import LineApiError, LineClient
from app.integrations.line.command_handler import handle
from app.integrations.line.notification_service import get_or_create_line_user
from app.models import LineUser, LineWebhookEvent

log = logging.getLogger(__name__)


def store_events(db: Session, payload: dict) -> list[int]:
    """Insert new events; returns ids of events that were not seen before."""
    ids: list[int] = []
    for ev in payload.get("events", []):
        event_id = ev.get("webhookEventId")
        if not event_id:
            continue
        src = ev.get("source") or {}
        stmt = (
            insert(LineWebhookEvent.__table__)
            .values(
                webhook_event_id=event_id,
                event_type=ev.get("type", "unknown"),
                line_user_id=src.get("userId"),
                payload=ev,
                processed=False,
                is_redelivery=bool((ev.get("deliveryContext") or {}).get("isRedelivery")),
                received_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["webhook_event_id"])
            .returning(LineWebhookEvent.__table__.c.id)
        )
        row = db.execute(stmt).first()
        if row:
            ids.append(row[0])
    db.commit()
    return ids


def process_event(db: Session, event_row: LineWebhookEvent, client: LineClient) -> None:
    ev = event_row.payload
    etype = ev.get("type")
    src = ev.get("source") or {}
    uid = src.get("userId")
    reply_token = ev.get("replyToken")
    messages: list[dict] | None = None

    if etype == "follow":
        name = None
        try:
            name = client.profile(uid).get("displayName") if uid and client.configured else None
        except LineApiError:
            pass
        lu = get_or_create_line_user(db, uid, name)
        lu.is_following = True
        lu.followed_at = datetime.now(UTC)
        lu.unfollowed_at = None
        messages = [mb.text_message(mb.WELCOME_TEXT)]
    elif etype == "unfollow":
        lu = db.scalar(select(LineUser).where(LineUser.line_user_id == uid)) if uid else None
        if lu:
            lu.is_following = False
            lu.unfollowed_at = datetime.now(UTC)
    elif etype == "message":
        msg = ev.get("message") or {}
        if uid:
            lu = get_or_create_line_user(db, uid)
            if not lu.is_following:  # message implies the user can be reached again
                lu.is_following = True
            if msg.get("type") == "text":
                messages = handle(db, lu, msg.get("text", ""))
            else:
                messages = [mb.text_message("I understand text commands only. Type /help")]
    elif etype == "postback":
        data = (ev.get("postback") or {}).get("data", "")
        if uid and data.startswith("cmd="):
            lu = get_or_create_line_user(db, uid)
            messages = handle(db, lu, data[4:])
    elif etype in ("join", "leave"):
        # Group/room membership; alerts are only sent to individual users.
        if etype == "join":
            messages = [mb.text_message("Hi! Stock alerts are delivered to individual users. Add me as a friend and type /help.")]
    # other event types are stored but need no action

    if messages and reply_token and client.configured:
        try:
            client.reply(reply_token, messages[:5])  # reply tokens are single use: never retried
        except LineApiError as exc:
            log.warning("LINE reply failed (%s): %s", exc.status_code, str(exc)[:200])
            event_row.error_message = f"reply failed: {str(exc)[:300]}"
    event_row.processed = True
    db.commit()


def process_stored_events(event_ids: list[int], client: LineClient | None = None) -> None:
    from app.db.session import session_scope

    client = client or LineClient()
    for eid in event_ids:
        with session_scope() as db:
            row = db.get(LineWebhookEvent, eid)
            if not row or row.processed:
                continue
            try:
                process_event(db, row, client)
            except Exception as exc:
                db.rollback()
                log.exception("webhook event %s failed", row.webhook_event_id)
                row = db.get(LineWebhookEvent, eid)
                if row:
                    row.error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
                    db.commit()
