"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, collections, portfolios, research, stocks, system
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.integrations.line.routes import router as line_router

log = logging.getLogger(__name__)


def bootstrap() -> None:
    """Idempotent startup tasks: admin user, exchanges, LINE admin recipient."""
    from app.core.security import ensure_admin_user
    from app.db.session import session_scope
    from app.integrations.line.notification_service import bootstrap_admin_recipient
    from app.pipeline.repository import ensure_exchanges

    try:
        with session_scope() as db:
            ensure_exchanges(db)
            ensure_admin_user(db)
            bootstrap_admin_recipient(db)
    except Exception:  # pragma: no cover - e.g. migrations not yet applied
        log.exception("bootstrap failed (have migrations been applied?)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_dir if s.environment != "test" else None)
    if s.environment != "test":
        bootstrap()
    if s.is_production and s.jwt_secret in ("change-me-in-env", "change-me"):
        raise RuntimeError("JWT_SECRET must be set in production")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=s.app_name,
        version="1.0.0",
        description="Long-term US stock monitoring: market data, indicators, signals, scores, portfolios and LINE alerts. Research tool only; never executes trades.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for r in (system.router, auth.router, stocks.router, collections.router, portfolios.router, research.router, line_router):
        app.include_router(r)
    return app


app = create_app()
