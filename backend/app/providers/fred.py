"""FRED macroeconomic data (optional; requires FRED_API_KEY).

Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

from datetime import date

from app.core.config import get_settings
from app.providers.base import MarketDataProvider, NotSupported
from app.providers.http import get_json

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "FEDFUNDS": "Federal Funds Effective Rate",
    "CPIAUCSL": "CPI (All Urban Consumers)",
    "UNRATE": "Unemployment Rate",
    "DGS10": "10-Year Treasury Yield",
    "DGS2": "2-Year Treasury Yield",
}


class FredProvider(MarketDataProvider):
    name = "fred"
    capabilities = frozenset({"macro"})

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else get_settings().fred_api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_series(self, series_id: str, start: date | None = None) -> list[dict]:
        if not self.api_key:
            raise NotSupported("fred: FRED_API_KEY not configured")
        params = {"series_id": series_id, "api_key": self.api_key, "file_type": "json"}
        if start:
            params["observation_start"] = start.isoformat()
        data = get_json(BASE_URL, params=params, throttle_key="fred", min_interval=0.5, cache_ttl=12 * 3600, cache_prefix="fred")
        out = []
        for o in data.get("observations", []):
            if o.get("value") in (None, ".", ""):
                continue  # FRED uses "." for missing values
            out.append({"date": o["date"], "value": float(o["value"])})
        return out

    def latest(self) -> dict[str, dict | None]:
        result: dict[str, dict | None] = {}
        for sid, label in SERIES.items():
            obs = self.get_series(sid)
            result[sid] = {"label": label, **obs[-1]} if obs else None
        return result
