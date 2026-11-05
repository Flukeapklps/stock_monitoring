"""LINE message quota monitoring (GET /v2/bot/message/quota and /quota/consumption).

Quota responses are cached for 10 minutes. Locally sent messages are added to the
cached usage so a burst can't overshoot the monthly limit between refreshes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.cache import cache_get_json, cache_set_json, incr_counter
from app.integrations.line.client import LineApiError, LineClient

log = logging.getLogger(__name__)
CACHE_KEY = "line:quota"
TTL = 600


class QuotaMonitor:
    def __init__(self, client: LineClient | None = None):
        self.client = client or LineClient()

    def _local_key(self) -> str:
        return f"line:sent:{datetime.now(UTC).strftime('%Y%m')}"

    def fetch(self, force: bool = False) -> dict:
        cached = None if force else cache_get_json(CACHE_KEY)
        if cached:
            return cached
        try:
            q = self.client.quota()
            c = self.client.consumption()
            data = {
                "type": q.get("type"),  # "none" (unlimited) | "limited"
                "limit": q.get("value"),
                "used": c.get("totalUsage"),
                "checked_at": datetime.now(UTC).isoformat(),
                "ok": True,
            }
        except LineApiError as exc:
            data = {"ok": False, "error": str(exc)[:300], "checked_at": datetime.now(UTC).isoformat()}
        cache_set_json(CACHE_KEY, data, TTL)
        from app.core.cache import backend

        try:
            backend().delete(self._local_key() + ":since")
        except Exception:  # pragma: no cover
            pass
        return data

    def record_sent(self, n: int = 1) -> None:
        incr_counter(self._local_key() + ":since", ttl=TTL)

    def can_send(self, db: Session | None = None, n: int = 1) -> bool:
        data = self.fetch()
        if not data.get("ok"):
            return True  # cannot verify: let LINE enforce (a 429 monthly-limit is handled as permanent)
        if data.get("type") != "limited" or data.get("limit") is None or data.get("used") is None:
            return True
        from app.core.cache import backend

        since = int(backend().get(self._local_key() + ":since") or 0)
        return data["used"] + since + n <= data["limit"]
