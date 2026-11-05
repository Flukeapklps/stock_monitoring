"""Core domain tables: users, exchanges, stocks, watchlists."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # admin | user | line
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York", nullable=False)
    line_link_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    score_weights: Mapped[dict | None] = mapped_column(JSONB)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    watchlists: Mapped[list[Watchlist]] = relationship(back_populates="user", cascade="all, delete-orphan")


class StockExchange(Base):
    __tablename__ = "stock_exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # NASDAQ, NYSE, NYSEAMERICAN, ARCA, INDEX
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mic: Mapped[str | None] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York", nullable=False)
    calendar_code: Mapped[str] = mapped_column(String(20), default="XNYS", nullable=False)


class Stock(TimestampMixin, Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    exchange_id: Mapped[int | None] = mapped_column(ForeignKey("stock_exchanges.id"))
    asset_type: Mapped[str] = mapped_column(String(20), default="EQUITY", nullable=False)  # EQUITY | ETF | INDEX
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(150))
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_source: Mapped[str | None] = mapped_column(String(30))
    first_trade_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_price_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shares_outstanding: Mapped[float | None] = mapped_column()

    exchange: Mapped[StockExchange | None] = relationship()


class Watchlist(TimestampMixin, Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="watchlists")
    items: Mapped[list[WatchlistItem]] = relationship(back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "stock_id"), Index("ix_watchlist_items_stock", "stock_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")
    stock: Mapped[Stock] = relationship()
