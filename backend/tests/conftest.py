"""Test configuration.

Database tests run against a real PostgreSQL database (TEST_DATABASE_URL) because
the application relies on PostgreSQL upserts (ON CONFLICT) and JSONB.
Market data in tests comes from `FakeProvider`, a deterministic synthetic series
used ONLY in automated tests — the application never falls back to fake data.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://stock:stock@localhost:5432/stock_test"))
os.environ.setdefault("JWT_SECRET", "test-secret-value")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test-channel-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("PROVIDER_PRIORITY", "fake")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password-123")

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from app.core.cache import reset_backend
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import set_engine
from app.market import calendar
from app.providers import registry
from app.providers.base import HistoryResult, MarketDataProvider, SymbolMetadata, SymbolNotFound

get_settings.cache_clear()


def synthetic_ohlcv(days: list[date], seed: int = 7, drift: float = 0.0006, vol: float = 0.012, start_price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, len(days))
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * (1 + rng.normal(0, 0.002, len(days)))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, len(days)))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, len(days)))
    volume = rng.integers(1_000_000, 5_000_000, len(days)).astype(float)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "adj_close": close, "volume": volume, "dividend": np.nan, "split_ratio": np.nan},
        index=days,
    )
    return df


class FakeProvider(MarketDataProvider):
    """Deterministic provider for tests. Series are generated per symbol from a seed."""

    name = "fake"
    capabilities = frozenset({"daily_history", "metadata"})

    def __init__(self, end: date | None = None, unknown: set[str] | None = None, params: dict | None = None):
        self.end = end
        self.calls: list[tuple[str, date, date]] = []
        self.unknown = unknown or {"ZZZZ"}
        self.params = params or {}
        self.overrides: dict[str, pd.DataFrame] = {}

    def series(self, symbol: str) -> pd.DataFrame:
        if symbol in self.overrides:
            return self.overrides[symbol]
        end = self.end or calendar.latest_completed_session()
        days = calendar.trading_days(end - timedelta(days=365 * 4), end)
        p = self.params.get(symbol, {})
        return synthetic_ohlcv(days, seed=sum(map(ord, symbol)), **p)

    def get_daily_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol in self.unknown:
            raise SymbolNotFound(symbol)
        s = self.series(symbol)
        s = s[(s.index >= start) & (s.index <= end)]
        return HistoryResult(symbol, self.name, s, adjusted_included=True, retrieved_at=datetime.now(UTC))

    def get_metadata(self, symbol):
        if symbol in self.unknown:
            raise SymbolNotFound(symbol)
        return SymbolMetadata(symbol=symbol, provider=self.name, name=f"{symbol} Inc.", exchange="NASDAQ", asset_type="ETF" if symbol in {"SPY", "QQQ", "VTI"} else "EQUITY", currency="USD")


@pytest.fixture(scope="session")
def engine():
    url = get_settings().database_url
    eng = create_engine(url)
    import app.models  # noqa: F401

    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(eng)
    set_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    from app.db.session import SessionLocal

    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    reset_backend(memory=True)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def fake_provider():
    p = FakeProvider()
    registry.register_override(p)
    yield p
    registry.clear_overrides()
