"""Resilient HTTP helper used by REST-based providers: retries with exponential
backoff, per-host minimum request interval and response caching."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import httpx

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import get_settings
from app.providers.base import ProviderDataError, ProviderUnavailable, RateLimited, SymbolNotFound

log = logging.getLogger(__name__)

_last_request: dict[str, float] = {}
_lock = threading.Lock()


def _throttle(key: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _lock:
        last = _last_request.get(key, 0.0)
        wait = last + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[key] = time.monotonic()


def cache_key(prefix: str, url: str, params: dict[str, Any] | None) -> str:
    items = sorted((k, str(v)) for k, v in (params or {}).items() if k.lower() not in {"apikey", "api_key"})
    digest = hashlib.sha1(f"{url}|{items}".encode()).hexdigest()
    return f"http:{prefix}:{digest}"


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    throttle_key: str,
    min_interval: float = 0.0,
    cache_ttl: int = 0,
    cache_prefix: str = "generic",
    client: httpx.Client | None = None,
    max_retries: int | None = None,
) -> Any:
    settings = get_settings()
    key = cache_key(cache_prefix, url, params)
    if cache_ttl:
        cached = cache_get_json(key)
        if cached is not None:
            return cached

    retries = settings.provider_max_retries if max_retries is None else max_retries
    owns_client = client is None
    client = client or httpx.Client(timeout=settings.http_timeout_seconds)
    try:
        attempt = 0
        while True:
            attempt += 1
            _throttle(throttle_key, min_interval)
            try:
                resp = client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > retries:
                    raise ProviderUnavailable(f"{throttle_key}: network error {type(exc).__name__}") from exc
                time.sleep(min(2 ** (attempt - 1), 16))
                continue
            if resp.status_code == 404:
                raise SymbolNotFound(f"{throttle_key}: not found ({url.split('?')[0]})")
            if resp.status_code == 429:
                if attempt > retries:
                    raise RateLimited(f"{throttle_key}: HTTP 429")
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 30))
                continue
            if resp.status_code >= 500:
                if attempt > retries:
                    raise ProviderUnavailable(f"{throttle_key}: HTTP {resp.status_code}")
                time.sleep(min(2 ** (attempt - 1), 16))
                continue
            if resp.status_code >= 400:
                raise ProviderDataError(f"{throttle_key}: HTTP {resp.status_code}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderDataError(f"{throttle_key}: invalid JSON") from exc
            if cache_ttl:
                cache_set_json(key, data, cache_ttl)
            return data
    finally:
        if owns_client:
            client.close()
