"""HTTP middleware: rate limiting and security headers."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.cache import incr_counter
from app.core.config import get_settings

EXEMPT_PATHS = {"/health", "/api/v1/health", "/api/v1/line/webhook"}


def client_ip(request: Request) -> str:
    # Behind Nginx, X-Real-IP is set by the proxy (see nginx config).
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP rate limiting backed by Redis (in-memory fallback)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        limit = get_settings().rate_limit_per_minute
        window = int(time.time() // 60)
        is_auth = request.url.path.startswith("/api/v1/auth/login")
        key = f"rl:{'auth' if is_auth else 'api'}:{client_ip(request)}:{window}"
        try:
            n = incr_counter(key, ttl=70)
        except Exception:  # pragma: no cover - never fail requests because the limiter is down
            n = 0
        effective = max(5, limit // 12) if is_auth else limit  # stricter for login attempts
        if n > effective:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp
