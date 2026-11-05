"""Provider registry: priority ordering, fallback and health tracking.

The registry never mixes data from different providers silently: each returned
result carries `provider`, and the caller stores it per record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ProviderStatus
from app.providers.alpha_vantage import AlphaVantageProvider
from app.providers.base import MarketDataProvider, NotSupported, ProviderError, SymbolNotFound
from app.providers.fred import FredProvider
from app.providers.sec_edgar import SecEdgarProvider
from app.providers.yahoo import YahooFinanceProvider

log = logging.getLogger(__name__)
T = TypeVar("T")

PROVIDER_LABELS = {
    "yahoo": "Yahoo Finance",
    "alpha_vantage": "Alpha Vantage",
    "sec_edgar": "SEC EDGAR",
    "fred": "FRED",
}

_FACTORIES: dict[str, Callable[[], MarketDataProvider]] = {
    "yahoo": YahooFinanceProvider,
    "alpha_vantage": AlphaVantageProvider,
    "sec_edgar": SecEdgarProvider,
    "fred": FredProvider,
}

_overrides: dict[str, MarketDataProvider] = {}


def register_override(provider: MarketDataProvider) -> None:
    """Tests use this to inject fake providers."""
    _overrides[provider.name] = provider


def clear_overrides() -> None:
    _overrides.clear()


def get_provider(name: str) -> MarketDataProvider:
    if name in _overrides:
        return _overrides[name]
    return _FACTORIES[name]()


def price_providers() -> list[MarketDataProvider]:
    out = []
    for name in get_settings().provider_priority_list:
        if name in _overrides or name in _FACTORIES:
            p = get_provider(name)
            if p.supports("daily_history") and p.is_configured():
                out.append(p)
    return out


# --- health tracking ----------------------------------------------------------
def _status_row(db: Session, name: str) -> ProviderStatus:
    row = db.scalar(select(ProviderStatus).where(ProviderStatus.provider == name))
    if row is None:
        row = ProviderStatus(provider=name, is_enabled=True, is_healthy=True, consecutive_failures=0, requests_today=0)
        db.add(row)
        db.flush()
    return row


def record_success(db: Session, name: str, requests: int = 1) -> None:
    row = _status_row(db, name)
    now = datetime.now(UTC)
    if row.quota_date != now.date():
        row.quota_date, row.requests_today = now.date(), 0
    row.requests_today += requests
    row.is_healthy, row.consecutive_failures, row.last_success_at = True, 0, now
    if name == "alpha_vantage":
        row.daily_quota = get_settings().alpha_vantage_daily_limit


def record_failure(db: Session, name: str, error: Exception) -> None:
    row = _status_row(db, name)
    now = datetime.now(UTC)
    if row.quota_date != now.date():
        row.quota_date, row.requests_today = now.date(), 0
    row.requests_today += 1
    row.consecutive_failures += 1
    row.last_failure_at = now
    row.last_error = f"{type(error).__name__}: {str(error)[:500]}"
    # SymbolNotFound is a data problem, not a provider outage
    if not isinstance(error, (SymbolNotFound, NotSupported)) and row.consecutive_failures >= 3:
        row.is_healthy = False


def with_fallback(
    db: Session | None,
    providers: list[MarketDataProvider],
    fn: Callable[[MarketDataProvider], T],
    what: str,
) -> tuple[T, str]:
    """Try providers in priority order; returns (result, provider_name)."""
    errors: list[str] = []
    for p in providers:
        try:
            result = fn(p)
            if db is not None:
                record_success(db, p.name)
            return result, p.name
        except ProviderError as exc:
            log.warning("provider %s failed for %s: %s", p.name, what, exc)
            errors.append(f"{p.name}: {exc}")
            if db is not None:
                record_failure(db, p.name, exc)
    raise ProviderError(f"all providers failed for {what}: {'; '.join(errors) or 'no provider configured'}")


def provider_health(db: Session) -> list[dict]:
    rows = {r.provider: r for r in db.scalars(select(ProviderStatus))}
    out = []
    today = date.today()
    for name in _FACTORIES:
        p = get_provider(name)
        r = rows.get(name)
        out.append(
            {
                "provider": name,
                "label": PROVIDER_LABELS.get(name, name),
                "configured": p.is_configured(),
                "capabilities": sorted(p.capabilities),
                "is_healthy": r.is_healthy if r else None,
                "last_success_at": r.last_success_at if r else None,
                "last_failure_at": r.last_failure_at if r else None,
                "last_error": r.last_error if r else None,
                "consecutive_failures": r.consecutive_failures if r else 0,
                "requests_today": r.requests_today if r and r.quota_date == today else 0,
                "daily_quota": r.daily_quota if r else None,
            }
        )
    return out
