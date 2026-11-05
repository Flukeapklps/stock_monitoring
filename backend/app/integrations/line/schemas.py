from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SendRequest(BaseModel):
    line_user_ids: list[str] | None = Field(None, description="Registered recipients; defaults to all followers")
    text: str | None = Field(None, max_length=5000)
    messages: list[dict[str, Any]] | None = None

    @field_validator("messages")
    @classmethod
    def _limit(cls, v):
        if v is not None and not 1 <= len(v) <= 5:
            raise ValueError("1-5 messages")
        return v


class TestRequest(BaseModel):
    line_user_id: str | None = None
    symbol: str | None = Field(None, description="Send a sample stock card for this symbol")


class AlertTestRequest(BaseModel):
    symbol: str
    signal: str = "ACCUMULATION_WATCH"
    line_user_id: str | None = None


class ReportRequest(BaseModel):
    line_user_id: str | None = Field(None, description="Send only to this recipient (ignores preferences)")


class PreferenceUpdate(BaseModel):
    line_user_id: str | None = None
    preferences: dict[str, bool] = Field(default_factory=dict)
    general: dict[str, Any] | None = None

    @field_validator("general")
    @classmethod
    def _general(cls, v):
        if v is None:
            return v
        allowed = {"enabled", "min_score", "rsi_low", "rsi_high", "cooldown_minutes", "max_per_day", "quiet_hours", "timezone", "symbols", "timeframes", "daily_report_time", "weekly_report_time"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        if "timezone" in v:
            from zoneinfo import ZoneInfo

            ZoneInfo(v["timezone"])
        qh = v.get("quiet_hours")
        if qh:
            import re

            for k in ("start", "end"):
                if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(qh.get(k, ""))):
                    raise ValueError("quiet_hours start/end must be HH:MM")
        if "symbols" in v:
            v["symbols"] = [str(s).upper().strip() for s in v["symbols"] if str(s).strip()][:200]
        for k in ("min_score", "rsi_low", "rsi_high"):
            if k in v and not 0 <= float(v[k]) <= 100:
                raise ValueError(f"{k} must be 0-100")
        return v


class AlertRuleIn(BaseModel):
    line_user_id: str | None = None
    stock_symbol: str
    rule_type: str
    conditions: dict[str, Any]
    cooldown_minutes: int = 1440
    enabled: bool = True


class HistoryItem(BaseModel):
    id: int
    line_user_id: str | None
    display_name: str | None
    stock_symbol: str | None
    notification_type: str
    status: str
    attempt_count: int
    http_status: int | None
    line_request_id: str | None
    error_message: str | None
    created_at: datetime
    sent_at: datetime | None
    preview: str
