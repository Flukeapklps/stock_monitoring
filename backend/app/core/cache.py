"""Small cache / counter facade over Redis with an in-process fallback.

Redis is preferred (shared between API and worker). When Redis is unreachable
(e.g. unit tests) an in-memory dict is used so the application still works.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)


class _MemoryBackend:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float | None, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            exp, val = item
            if exp is not None and exp < time.time():
                del self._data[key]
                return None
            return val

    def set(self, key: str, value: str, ttl: int | None = None, nx: bool = False) -> bool:
        with self._lock:
            if nx:
                cur = self._data.get(key)
                if cur and (cur[0] is None or cur[0] >= time.time()):
                    return False
            self._data[key] = (time.time() + ttl if ttl else None, value)
            return True

    def incr(self, key: str, ttl: int | None = None) -> int:
        with self._lock:
            exp, val = self._data.get(key, (None, "0"))
            if exp is not None and exp < time.time():
                exp, val = None, "0"
            n = int(val) + 1
            self._data[key] = (exp or (time.time() + ttl if ttl else None), str(n))
            return n

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def ping(self) -> bool:
        return True


class _RedisBackend:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
        self._r.ping()

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl: int | None = None, nx: bool = False) -> bool:
        return bool(self._r.set(key, value, ex=ttl, nx=nx))

    def incr(self, key: str, ttl: int | None = None) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        if ttl:
            pipe.expire(key, ttl, nx=True)
        return int(pipe.execute()[0])

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def ping(self) -> bool:
        return bool(self._r.ping())


_backend: Any = None
_backend_lock = threading.Lock()


def backend() -> Any:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                settings = get_settings()
                if settings.environment == "test":
                    _backend = _MemoryBackend()
                else:
                    try:
                        _backend = _RedisBackend(settings.redis_url)
                    except Exception as exc:  # pragma: no cover - depends on infra
                        log.warning("Redis unavailable (%s); using in-memory cache", type(exc).__name__)
                        _backend = _MemoryBackend()
    return _backend


def reset_backend(memory: bool = True) -> None:
    global _backend
    _backend = _MemoryBackend() if memory else None


def cache_get_json(key: str) -> Any | None:
    try:
        raw = backend().get(key)
    except Exception:  # pragma: no cover
        return None
    return json.loads(raw) if raw else None


def cache_set_json(key: str, value: Any, ttl: int) -> None:
    try:
        backend().set(key, json.dumps(value, default=str), ttl=ttl)
    except Exception:  # pragma: no cover
        log.debug("cache set failed for %s", key)


def acquire_once(key: str, ttl: int) -> bool:
    """Atomic set-if-absent; used for dedupe/locks."""
    return backend().set(key, "1", ttl=ttl, nx=True)


def incr_counter(key: str, ttl: int) -> int:
    return backend().incr(key, ttl=ttl)
