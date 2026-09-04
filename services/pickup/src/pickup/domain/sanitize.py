"""Sanitized error helpers — no payload or secret leakage."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization|jwt|otp)\s*[:=]\s*\S+"),
    re.compile(r"(?i)sk_(live|test)_[A-Za-z0-9]+"),
    re.compile(r"(?i)(postgres(ql)?|mysql|mongodb|redis|amqp)://\S+"),
)

_REDACTED = "[redacted]"


def sanitize_error_message(message: str, *, max_length: int = 512) -> str:
    """Return a log-safe error string without secrets or large payloads."""
    cleaned = message.replace("\n", " ").strip()
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    if len(cleaned) > max_length:
        return f"{cleaned[: max_length - 3]}..."
    return cleaned
