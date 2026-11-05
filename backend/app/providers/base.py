"""Provider abstraction layer.

Every provider returns data tagged with its own name so the pipeline can record
the source of each record. Providers never write to the database themselves.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume", "dividend", "split_ratio"]


class ProviderError(Exception):
    """Base class for provider failures."""

    retryable = True


class ProviderUnavailable(ProviderError):
    """Network failure / 5xx / timeout."""


class RateLimited(ProviderError):
    """Provider quota or rate limit reached."""


class SymbolNotFound(ProviderError):
    retryable = False


class NotSupported(ProviderError):
    """Capability not supported by provider or by the configured plan."""

    retryable = False


class ProviderDataError(ProviderError):
    """Malformed response."""

    retryable = False


@dataclass
class HistoryResult:
    symbol: str
    provider: str
    frame: pd.DataFrame  # index: datetime.date (session date), columns: OHLCV_COLUMNS
    adjusted_included: bool
    retrieved_at: datetime
    warnings: list[str] = field(default_factory=list)


@dataclass
class Quote:
    symbol: str
    provider: str
    price: float | None
    change: float | None
    change_pct: float | None
    previous_close: float | None
    as_of: datetime | None
    trading_day: date | None
    data_status: str  # END_OF_DAY | DELAYED | LIVE (only if provider guarantees it)
    volume: int | None = None


@dataclass
class SymbolMetadata:
    symbol: str
    provider: str
    name: str | None = None
    exchange: str | None = None
    asset_type: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    shares_outstanding: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class MarketDataProvider(abc.ABC):
    """Interface implemented by all market data providers."""

    name: str = "base"
    #: capabilities: daily_history, weekly_history, monthly_history, quote, metadata,
    #: dividends, splits, earnings, fundamentals, filings, macro
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def is_configured(self) -> bool:
        return True

    # --- price data ---------------------------------------------------------
    def get_daily_history(self, symbol: str, start: date, end: date) -> HistoryResult:
        raise NotSupported(f"{self.name} does not provide daily history")

    def get_daily_history_batch(self, symbols: list[str], start: date, end: date) -> dict[str, HistoryResult]:
        """Default batch implementation loops; providers may override with true batching."""
        out: dict[str, HistoryResult] = {}
        for s in symbols:
            out[s] = self.get_daily_history(s, start, end)
        return out

    def get_quote(self, symbol: str) -> Quote:
        raise NotSupported(f"{self.name} does not provide quotes")

    def get_metadata(self, symbol: str) -> SymbolMetadata:
        raise NotSupported(f"{self.name} does not provide metadata")

    def health_check(self) -> bool:
        return True


def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)
