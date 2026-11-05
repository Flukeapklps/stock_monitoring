"""Alpha Vantage provider (official REST API).

Docs: https://www.alphavantage.co/documentation/

Plan limits matter: the free tier documents 25 requests/day, `outputsize=full`
and adjusted daily series are premium, and US real-time / 15-minute delayed
quotes are not part of the free tier. This provider therefore:
  * enforces a daily request budget (ALPHA_VANTAGE_DAILY_LIMIT),
  * spaces requests (ALPHA_VANTAGE_MIN_INTERVAL_SECONDS),
  * caches successful responses,
  * detects "Note"/"Information"/"Error Message" payloads (rate-limit / premium / bad symbol),
  * labels quotes END_OF_DAY unless a premium real-time entitlement is configured.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pandas as pd

from app.core.cache import cache_get_json, cache_set_json, incr_counter
from app.core.config import get_settings
from app.providers.base import (
    OHLCV_COLUMNS,
    HistoryResult,
    MarketDataProvider,
    NotSupported,
    ProviderDataError,
    Quote,
    RateLimited,
    SymbolMetadata,
    SymbolNotFound,
)
from app.providers.http import cache_key, get_json

log = logging.getLogger(__name__)
BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageProvider(MarketDataProvider):
    name = "alpha_vantage"
    capabilities = frozenset(
        {"daily_history", "weekly_history", "monthly_history", "quote", "metadata", "earnings", "dividends", "splits"}
    )

    def __init__(self, api_key: str | None = None, outputsize: str = "compact") -> None:
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.alpha_vantage_api_key
        self.daily_limit = s.alpha_vantage_daily_limit
        self.min_interval = s.alpha_vantage_min_interval_seconds
        self.outputsize = outputsize

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # ---------------------------------------------------------------------
    def quota_used_today(self) -> int:
        from app.core.cache import backend

        raw = backend().get(self._quota_key())
        return int(raw) if raw else 0

    def _quota_key(self) -> str:
        return f"quota:alpha_vantage:{datetime.now(UTC).date().isoformat()}"

    def _call(self, function: str, ttl: int, **params) -> dict:
        if not self.api_key:
            raise NotSupported("alpha_vantage: ALPHA_VANTAGE_API_KEY not configured")
        query = {"function": function, **params, "apikey": self.api_key}
        key = cache_key("av", BASE_URL, query)
        cached = cache_get_json(key)
        if cached is not None:
            return cached
        if self.quota_used_today() >= self.daily_limit:
            raise RateLimited(f"alpha_vantage: daily budget of {self.daily_limit} requests exhausted")
        incr_counter(self._quota_key(), ttl=26 * 3600)
        data = get_json(BASE_URL, params=query, throttle_key="alpha_vantage", min_interval=self.min_interval)
        if not isinstance(data, dict):
            raise ProviderDataError("alpha_vantage: unexpected payload")
        if "Error Message" in data:
            raise SymbolNotFound(f"alpha_vantage: {data['Error Message'][:200]}")
        for k in ("Note", "Information"):
            if k in data:
                msg = str(data[k])
                if "premium" in msg.lower():
                    raise NotSupported(f"alpha_vantage: premium endpoint/parameter: {msg[:200]}")
                raise RateLimited(f"alpha_vantage: {msg[:200]}")
        if not data:
            raise SymbolNotFound(f"alpha_vantage: empty response for {function}")
        cache_set_json(key, data, ttl)
        return data

    @staticmethod
    def _parse_series(data: dict, symbol: str) -> pd.DataFrame:
        series_key = next((k for k in data if "Time Series" in k), None)
        if not series_key:
            raise ProviderDataError(f"alpha_vantage: no time series for {symbol}")
        rows = []
        for d, v in data[series_key].items():
            rows.append(
                {
                    "date": date.fromisoformat(d),
                    "open": float(v["1. open"]),
                    "high": float(v["2. high"]),
                    "low": float(v["3. low"]),
                    "close": float(v["4. close"]),
                    "adj_close": float(v["5. adjusted close"]) if "5. adjusted close" in v else None,
                    "volume": int(float(v.get("5. volume") or v.get("6. volume") or 0)),
                    "dividend": float(v["7. dividend amount"]) if "7. dividend amount" in v else None,
                    "split_ratio": float(v["8. split coefficient"]) if "8. split coefficient" in v else None,
                }
            )
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df["dividend"] = df["dividend"].where(df["dividend"].fillna(0) != 0)
        df["split_ratio"] = df["split_ratio"].where(~df["split_ratio"].isin([0.0, 1.0]))
        return df[OHLCV_COLUMNS]

    def get_daily_history(self, symbol: str, start: date, end: date) -> HistoryResult:
        data = self._call("TIME_SERIES_DAILY", ttl=6 * 3600, symbol=symbol, outputsize=self.outputsize)
        df = self._parse_series(data, symbol)
        df = df[(df.index >= start) & (df.index <= end)]
        warnings = ["TIME_SERIES_DAILY is unadjusted; adj_close unavailable from this endpoint"]
        if self.outputsize == "compact":
            warnings.append("compact output: last 100 sessions only")
        return HistoryResult(symbol, self.name, df, adjusted_included=False, retrieved_at=datetime.now(UTC), warnings=warnings)

    def get_weekly_history(self, symbol: str) -> pd.DataFrame:
        return self._parse_series(self._call("TIME_SERIES_WEEKLY", ttl=24 * 3600, symbol=symbol), symbol)

    def get_monthly_history(self, symbol: str) -> pd.DataFrame:
        return self._parse_series(self._call("TIME_SERIES_MONTHLY", ttl=24 * 3600, symbol=symbol), symbol)

    def get_quote(self, symbol: str) -> Quote:
        data = self._call("GLOBAL_QUOTE", ttl=15 * 60, symbol=symbol).get("Global Quote") or {}
        if not data:
            raise SymbolNotFound(f"alpha_vantage: no quote for {symbol}")
        price = float(data["05. price"])
        prev = float(data["08. previous close"]) if data.get("08. previous close") else None
        change_pct = data.get("10. change percent", "").rstrip("%")
        day = date.fromisoformat(data["07. latest trading day"]) if data.get("07. latest trading day") else None
        return Quote(
            symbol=symbol,
            provider=self.name,
            price=price,
            change=float(data["09. change"]) if data.get("09. change") else None,
            change_pct=float(change_pct) if change_pct else None,
            previous_close=prev,
            as_of=datetime.now(UTC),
            trading_day=day,
            # Free tier does not include real-time/15-min delayed US data: never label as LIVE.
            data_status="END_OF_DAY",
            volume=int(data["06. volume"]) if data.get("06. volume") else None,
        )

    def get_metadata(self, symbol: str) -> SymbolMetadata:
        data = self._call("OVERVIEW", ttl=7 * 24 * 3600, symbol=symbol)
        if "Symbol" not in data:
            raise SymbolNotFound(f"alpha_vantage: no overview for {symbol}")
        so = data.get("SharesOutstanding")
        return SymbolMetadata(
            symbol=symbol,
            provider=self.name,
            name=data.get("Name"),
            exchange=data.get("Exchange"),
            asset_type=(data.get("AssetType") or "").upper().replace("COMMON STOCK", "EQUITY") or None,
            currency=data.get("Currency"),
            sector=(data.get("Sector") or "").title() or None,
            industry=(data.get("Industry") or "").title() or None,
            shares_outstanding=float(so) if so and so != "None" else None,
            extra={k: data.get(k) for k in ("CIK", "PERatio", "ForwardPE", "PriceToSalesRatioTTM", "PriceToBookRatio", "EVToEBITDA")},
        )

    def get_earnings(self, symbol: str) -> dict:
        return self._call("EARNINGS", ttl=24 * 3600, symbol=symbol)

    def get_dividends(self, symbol: str) -> dict:
        return self._call("DIVIDENDS", ttl=24 * 3600, symbol=symbol)

    def get_splits(self, symbol: str) -> dict:
        return self._call("SPLITS", ttl=24 * 3600, symbol=symbol)
