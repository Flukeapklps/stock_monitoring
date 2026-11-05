"""Retry policy for LINE API calls.

Retryable: network errors/timeouts, HTTP 5xx, HTTP 429 *rate limit* (not the
monthly quota error). Permanent: 400 (bad request / invalid recipient), 401/403
(invalid token / not permitted), 404, monthly quota exhausted.
Retries reuse the same X-Line-Retry-Key so LINE de-duplicates accepted requests
(a duplicate returns 409 with `x-line-accepted-request-id`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 30 * 60


def backoff_delay(attempt: int) -> int:
    """Exponential backoff: 30s, 60s, 120s, ... capped at 30 minutes."""
    return min(BASE_DELAY_SECONDS * (2 ** max(attempt - 1, 0)), MAX_DELAY_SECONDS)


def next_attempt_at(attempt: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(seconds=backoff_delay(attempt))


def classify_status(status_code: int | None, message: str = "") -> str:
    """Return RETRYABLE | PERMANENT | QUOTA | UNKNOWN."""
    if status_code is None:
        return "UNKNOWN"  # timeout/network: request may or may not have been accepted
    if status_code == 429:
        return "QUOTA" if "monthly limit" in message.lower() else "RETRYABLE"
    if status_code >= 500:
        return "RETRYABLE"
    if 400 <= status_code < 500:
        return "PERMANENT"
    return "RETRYABLE"
