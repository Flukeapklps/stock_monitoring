"""Portfolios, transactions, holdings, alert rules, generic notification history."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.core import Stock


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    max_drawdown_alert_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    max_position_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))

    transactions: Mapped[list[PortfolioTransaction]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    holdings: Mapped[list[PortfolioHolding]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioTransaction(Base):
    __tablename__ = "portfolio_transactions"
    __table_args__ = (Index("ix_ptx_portfolio_date", "portfolio_id", "trade_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    tx_type: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY | SELL | DIVIDEND
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    fees: Mapped[float] = mapped_column(Numeric(18, 6), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    portfolio: Mapped[Portfolio] = relationship(back_populates="transactions")
    stock: Mapped[Stock] = relationship()


class PortfolioHolding(TimestampMixin, Base):
    """Current position per stock. Maintained from transactions or entered manually."""

    __tablename__ = "portfolio_holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "stock_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    average_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    realized_pl: Mapped[float] = mapped_column(Numeric(20, 6), default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")
    stock: Mapped[Stock] = relationship()


class AlertRule(TimestampMixin, Base):
    """Web-user alert rules (price target / risk threshold / score / signal subscriptions)."""

    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=1440, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationHistory(Base):
    """Channel-agnostic alert events. `dedupe_key` prevents the same alert being raised twice."""

    __tablename__ = "notification_history"
    __table_args__ = (
        UniqueConstraint("recipient_key", "dedupe_key", name="uq_notification_recipient_dedupe"),
        Index("ix_notification_created", "created_at"),
        Index("ix_notification_recipient_category", "recipient_key", "category", "stock_symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recipient_key: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. line:<line_users.id>
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    stock_symbol: Mapped[str | None] = mapped_column(String(20))
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
