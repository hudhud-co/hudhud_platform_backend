"""Deterministic UUID and timestamp formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID


def format_uuid(value: UUID) -> str:
    """RFC 4122 lowercase UUID string."""
    return str(value).lower()


def format_utc_datetime(value: datetime) -> str:
    """RFC 3339 UTC with millisecond precision and ``Z`` suffix."""
    if value.tzinfo is None:
        msg = "occurred_at must be timezone-aware UTC"
        raise ValueError(msg)
    utc = value.astimezone(UTC)
    millis = utc.microsecond // 1000
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def parse_utc_datetime(value: str) -> datetime:
    """Parse RFC 3339 UTC datetime strings produced by :func:`format_utc_datetime`."""
    if not value.endswith("Z"):
        msg = "timestamp must use UTC Z suffix"
        raise ValueError(msg)
    normalized = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        msg = "timestamp must be timezone-aware"
        raise ValueError(msg)
    return parsed.astimezone(UTC)
