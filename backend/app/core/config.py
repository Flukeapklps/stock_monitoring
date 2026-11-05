"""Application configuration loaded from environment variables.

All secrets (API keys, LINE tokens, JWT secret, DB passwords) come from the
environment only. Nothing secret is hardcoded here.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General -----------------------------------------------------------
    app_name: str = "US Stock Long-Term Intelligence Platform"
    environment: str = Field(default="development", description="development | production | test")
    log_level: str = "INFO"
    log_dir: str = "logs"
    public_base_url: str = Field(
        default="http://localhost:3000",
        description="Public HTTPS URL of the web dashboard; used for LINE action buttons.",
    )

    # --- Database / cache ----------------------------------------------------
    database_url: str = "postgresql+psycopg://stock:stock@localhost:5432/stock"
    redis_url: str = "redis://localhost:6379/0"

    # --- Security ------------------------------------------------------------
    jwt_secret: str = Field(default="change-me-in-env", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 12
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 120
    admin_email: str | None = None
    admin_password: str | None = None
    allow_registration: bool = False

    # --- Market data providers ----------------------------------------------
    provider_priority: str = "yahoo,alpha_vantage"
    alpha_vantage_api_key: str | None = None
    alpha_vantage_daily_limit: int = 25
    alpha_vantage_min_interval_seconds: float = 15.0
    sec_user_agent: str = Field(
        default="StockMonitor/1.0 admin@example.com",
        description="SEC requires a descriptive User-Agent with contact information.",
    )
    sec_min_interval_seconds: float = 0.15  # SEC fair access: max 10 req/s
    fred_api_key: str | None = None
    http_timeout_seconds: float = 20.0
    provider_max_retries: int = 3
    initial_history_years: int = 10
    cache_ttl_seconds: int = 6 * 3600

    # --- Market data quality -------------------------------------------------
    abnormal_move_threshold: float = 0.35  # 35% daily move flagged for review
    benchmarks: str = "SPY,QQQ,VTI"
    market_overview_symbols: str = "^GSPC,^IXIC,^DJI,^RUT,^VIX,SPY,QQQ"
    default_universe: str = "AAPL,MSFT,NVDA,GOOGL,AMZN,META,BRK-B,JPM,V,JNJ,SPY,QQQ,VTI"

    # --- Scheduler ------------------------------------------------------------
    scheduler_enabled: bool = True
    scheduler_timezone: str = "America/New_York"
    daily_job_time: str = "17:30"  # ET, after the 16:00 close (+ provider settle time)
    weekly_job_time: str = "18:30"
    monthly_job_time: str = "19:00"
    notification_dispatch_seconds: int = 30
    sec_filings_job_time: str = "20:00"

    # --- LINE Messaging API --------------------------------------------------
    line_channel_access_token: str | None = None
    line_channel_secret: str | None = None
    line_user_id: str | None = None
    line_webhook_url: str | None = None
    line_enabled: bool = True
    line_default_notification_enabled: bool = True
    line_alert_cooldown_minutes: int = 1440
    line_max_retry_attempts: int = 3
    line_api_base: str = "https://api.line.me"
    line_daily_report_time: str = "17:45"
    line_weekly_report_time: str = "18:45"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # Convenience accessors -------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def provider_priority_list(self) -> list[str]:
        return [p.strip() for p in self.provider_priority.split(",") if p.strip()]

    @property
    def benchmark_list(self) -> list[str]:
        return [s.strip().upper() for s in self.benchmarks.split(",") if s.strip()]

    @property
    def market_overview_list(self) -> list[str]:
        return [s.strip().upper() for s in self.market_overview_symbols.split(",") if s.strip()]

    @property
    def default_universe_list(self) -> list[str]:
        return [s.strip().upper() for s in self.default_universe.split(",") if s.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def line_configured(self) -> bool:
        return bool(self.line_channel_access_token and self.line_channel_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
