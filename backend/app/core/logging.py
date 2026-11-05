"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler

_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE),
    re.compile(r"(apikey=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+", re.IGNORECASE),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(r"\1***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        return redact(super().format(record))


def configure_logging(level: str = "INFO", log_dir: str | None = None) -> None:
    root = logging.getLogger()
    if getattr(root, "_stock_configured", False):
        return
    fmt = RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.handlers = [stream]
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            fh = RotatingFileHandler(os.path.join(log_dir, "app.log"), maxBytes=10_000_000, backupCount=5)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            root.warning("Could not create log directory %s; logging to stdout only", log_dir)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    root._stock_configured = True  # type: ignore[attr-defined]
