"""LINE Messaging API tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow

NOTIFICATION_STATUSES = ("QUEUED", "SENDING", "ACCEPTED", "FAILED", "UNKNOWN", "SKIPPED")


class LineUser(TimestampMixin, Base):
    __tablename__ = "line_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_user_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_following: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Platform user this LINE account acts as (watchlists, portfolios). Created on follow.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok", nullable=False)
    followed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unfollowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LineNotificationPreference(TimestampMixin, Base):
    __tablename__ = "line_notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "notification_type", name="uq_line_pref_user_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("line_users.id", ondelete="CASCADE"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class LineNotificationHistory(Base):
    __tablename__ = "line_notification_history"
    __table_args__ = (
        Index("ix_line_hist_status_next", "status", "next_attempt_at"),
        Index("ix_line_hist_user_created", "user_id", "created_at"),
        UniqueConstraint("retry_key", name="uq_line_hist_retry_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("line_users.id", ondelete="SET NULL"))
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notification_history.id", ondelete="SET NULL"))
    stock_symbol: Mapped[str | None] = mapped_column(String(20))
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message_payload: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_key: Mapped[str] = mapped_column(String(36), nullable=False)  # X-Line-Retry-Key (UUID)
    line_request_id: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    api_response: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LineWebhookEvent(Base):
    __tablename__ = "line_webhook_events"
    __table_args__ = (Index("ix_line_webhook_received", "received_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    webhook_event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    line_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_redelivery: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LineAlertRule(TimestampMixin, Base):
    __tablename__ = "line_alert_rules"
    __table_args__ = (Index("ix_line_alert_rules_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("line_users.id", ondelete="CASCADE"), nullable=False)
    stock_symbol: Mapped[str | None] = mapped_column(String(20))
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    conditions_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_state: Mapped[str | None] = mapped_column(String(20))  # for crossing detection: ABOVE | BELOW
