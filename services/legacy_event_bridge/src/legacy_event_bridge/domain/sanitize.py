"""Sanitized error helpers — no payload or secret leakage."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)jwt\s+\S+"),
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


def safe_repr(value: object) -> str:
    """Avoid repr that might include raw payload values."""
    return f"<{type(value).__name__}>"
