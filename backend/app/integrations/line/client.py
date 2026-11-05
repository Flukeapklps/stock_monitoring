"""Thin client for the official LINE Messaging API.

Reference: https://developers.line.biz/en/reference/messaging-api/
The channel access token never leaves the server and is never logged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.line.retry_handler import classify_status

log = logging.getLogger(__name__)


class LineApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, kind: str = "UNKNOWN", body: Any = None, request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind  # RETRYABLE | PERMANENT | QUOTA | UNKNOWN | NOT_CONFIGURED
        self.body = body
        self.request_id = request_id


@dataclass
class LineResponse:
    status_code: int
    request_id: str | None
    body: Any
    accepted_request_id: str | None = None  # set on 409 duplicate retry key


class LineClient:
    def __init__(self, access_token: str | None = None, base_url: str | None = None, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.access_token = access_token if access_token is not None else s.line_channel_access_token
        self.base_url = (base_url or s.line_api_base).rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self.transport)

    def _request(self, method: str, path: str, *, json: Any = None, retry_key: str | None = None) -> LineResponse:
        if not self.access_token:
            raise LineApiError("LINE_CHANNEL_ACCESS_TOKEN not configured", kind="NOT_CONFIGURED")
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key
        try:
            with self._client() as c:
                resp = c.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise LineApiError(f"timeout calling LINE {path}", None, "UNKNOWN") from exc
        except httpx.TransportError as exc:
            raise LineApiError(f"network error calling LINE {path}: {type(exc).__name__}", None, "RETRYABLE") from exc
        req_id = resp.headers.get("x-line-request-id")
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {"raw": resp.text[:500]}
        if resp.status_code == 409 and retry_key:
            # Same retry key already accepted -> treat as success
            return LineResponse(resp.status_code, req_id, body, accepted_request_id=resp.headers.get("x-line-accepted-request-id"))
        if resp.status_code >= 400:
            msg = body.get("message", "") if isinstance(body, dict) else str(body)
            kind = classify_status(resp.status_code, msg)
            raise LineApiError(f"LINE API {resp.status_code}: {msg}"[:500], resp.status_code, kind, body, req_id)
        return LineResponse(resp.status_code, req_id, body)

    # --- messaging --------------------------------------------------------------
    def push(self, to: str, messages: list[dict], retry_key: str | None = None, notification_disabled: bool = False) -> LineResponse:
        return self._request(
            "POST", "/v2/bot/message/push", json={"to": to, "messages": messages, "notificationDisabled": notification_disabled}, retry_key=retry_key or str(uuid.uuid4())
        )

    def reply(self, reply_token: str, messages: list[dict]) -> LineResponse:
        # reply tokens are single-use and short-lived: never retried
        return self._request("POST", "/v2/bot/message/reply", json={"replyToken": reply_token, "messages": messages})

    def multicast(self, to: list[str], messages: list[dict], retry_key: str | None = None) -> LineResponse:
        if not 1 <= len(to) <= 500:
            raise LineApiError("multicast requires 1-500 recipients", kind="PERMANENT")
        return self._request("POST", "/v2/bot/message/multicast", json={"to": to, "messages": messages}, retry_key=retry_key or str(uuid.uuid4()))

    def validate_push(self, messages: list[dict]) -> LineResponse:
        """Server-side validation of message objects (no message is sent)."""
        return self._request("POST", "/v2/bot/message/validate/push", json={"messages": messages})

    # --- account ---------------------------------------------------------------------
    def quota(self) -> dict:
        return self._request("GET", "/v2/bot/message/quota").body

    def consumption(self) -> dict:
        return self._request("GET", "/v2/bot/message/quota/consumption").body

    def profile(self, user_id: str) -> dict:
        return self._request("GET", f"/v2/bot/profile/{user_id}").body

    def bot_info(self) -> dict:
        return self._request("GET", "/v2/bot/info").body

    def webhook_endpoint(self) -> dict:
        return self._request("GET", "/v2/bot/channel/webhook/endpoint").body
