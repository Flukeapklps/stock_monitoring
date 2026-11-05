"""Market data, fundamentals, filings, indicators, signals and scores."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base, utcnow

DATA_STATUSES = ("LIVE", "DELAYED", "END_OF_DAY", "HISTORICAL", "STALE", "UNAVAILABLE")


class _OhlcvMixin:
    """Shared OHLCV columns. Prices are stored as NUMERIC to avoid float drift."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    @declared_attr
    def stock_id(cls) -> Mapped[int]:  # noqa: N805
        return mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    # UTC timestamp of the bar's close (session close for daily bars).
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(18, 6))
    high: Mapped[float | None] = mapped_column(Numeric(18, 6))
    low: Mapped[float | None] = mapped_column(Numeric(18, 6))
    close: Mapped[float | None] = mapped_column(Numeric(18, 6))
    adj_close: Mapped[float | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    data_status: Mapped[str] = mapped_column(String(20), nullable=False)


class MarketDataDaily(_OhlcvMixin, Base):
    __tablename__ = "market_data_daily"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_market_data_daily_stock_date"),
        Index("ix_market_data_daily_date", "trade_date"),
    )
    dividend: Mapped[float | None] = mapped_column(Numeric(18, 6))
    split_ratio: Mapped[float | None] = mapped_column(Numeric(18, 8))


class MarketDataWeekly(_OhlcvMixin, Base):
    """Weekly bars aggregated from daily bars; trade_date = last session of the week."""

    __tablename__ = "market_data_weekly"
    __table_args__ = (UniqueConstraint("stock_id", "trade_date", name="uq_market_data_weekly_stock_date"),)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MarketDataMonthly(_OhlcvMixin, Base):
    __tablename__ = "market_data_monthly"
    __table_args__ = (UniqueConstraint("stock_id", "trade_date", name="uq_market_data_monthly_stock_date"),)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FundamentalData(Base):
    """One row per (stock, metric, period). Reported values and calculated metrics are distinguished."""

    __tablename__ = "fundamental_data"
    __table_args__ = (
        UniqueConstraint("stock_id", "metric", "period_end", "period_type", name="uq_fundamental_metric_period"),
        Index("ix_fundamental_stock_metric", "stock_id", "metric"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    metric: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # FY | Q | TTM | INSTANT
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(4))
    form: Mapped[str | None] = mapped_column(String(10))
    filed_date: Mapped[date | None] = mapped_column(Date)
    accession_number: Mapped[str | None] = mapped_column(String(25))
    is_calculated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_tag: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SecFiling(Base):
    __tablename__ = "sec_filings"
    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_sec_filings_accession"),
        Index("ix_sec_filings_stock_date", "stock_id", "filing_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(25), nullable=False)
    form: Mapped[str] = mapped_column(String(15), nullable=False)
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IndicatorValue(Base):
    """Indicator snapshot per bar (wide JSONB). Values computed only from data up to trade_date."""

    __tablename__ = "indicator_values"
    __table_args__ = (
        UniqueConstraint("stock_id", "timeframe", "trade_date", name="uq_indicator_stock_tf_date"),
        Index("ix_indicator_stock_tf_date", "stock_id", "timeframe", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)  # DAILY | WEEKLY | MONTHLY
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InvestmentSignal(Base):
    __tablename__ = "investment_signals"
    __table_args__ = (
        UniqueConstraint("stock_id", "timeframe", "signal_date", "signal_type", name="uq_signal_stock_tf_date_type"),
        Index("ix_signals_date", "signal_date"),
        Index("ix_signals_type", "signal_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    confirmation_status: Mapped[str] = mapped_column(String(20), nullable=False)  # CONFIRMED | PENDING | UNCONFIRMED
    indicator_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    data_status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InvestmentScore(Base):
    __tablename__ = "investment_scores"
    __table_args__ = (UniqueConstraint("stock_id", "score_date", name="uq_score_stock_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    score_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_score: Mapped[float | None] = mapped_column(Float)
    classification: Mapped[str | None] = mapped_column(String(40))
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProviderStatus(Base):
    __tablename__ = "provider_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quota_date: Mapped[date | None] = mapped_column(Date)
    daily_quota: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DataQualityLog(Base):
    __tablename__ = "data_quality_logs"
    __table_args__ = (Index("ix_dq_stock_created", "stock_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # INFO | WARNING | ERROR
    trade_date: Mapped[date | None] = mapped_column(Date)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (Index("ix_system_logs_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    component: Mapped[str] = mapped_column(String(50), nullable=False)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
