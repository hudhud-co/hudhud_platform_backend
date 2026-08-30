"""W3C Trace Context validation and normalization."""

from __future__ import annotations

import re
from enum import StrEnum

from event_envelope.errors import EnvelopeValidationError

_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-fA-F]{32})-([0-9a-fA-F]{16})-([0-9a-fA-F]{2})$"
)

_ALL_ZERO_TRACE_ID = "0" * 32
_ALL_ZERO_PARENT_ID = "0" * 16


class TraceContextPolicy(StrEnum):
    """How invalid or absent traceparent values are handled."""

    REJECT = "reject"
    NORMALIZE = "normalize"
    IGNORE = "ignore"


def validate_traceparent(value: str | None) -> str | None:
    """Validate W3C traceparent; reject all-zero identifiers.

    Returns the normalized lowercase traceparent or ``None`` when absent.
    """
    if value is None:
        return None
    normalized = normalize_traceparent(value)
    if normalized is None:
        raise EnvelopeValidationError("traceparent", "invalid W3C traceparent format")
    return normalized


def normalize_traceparent(value: str) -> str | None:
    """Normalize a traceparent to lowercase or return ``None`` when invalid."""
    stripped = value.strip()
    match = _TRACEPARENT_RE.match(stripped)
    if not match:
        return None
    trace_id, parent_id, flags = match.groups()
    trace_id = trace_id.lower()
    parent_id = parent_id.lower()
    flags = flags.lower()
    if trace_id == _ALL_ZERO_TRACE_ID or parent_id == _ALL_ZERO_PARENT_ID:
        return None
    return f"00-{trace_id}-{parent_id}-{flags}"


def apply_trace_context_policy(
    value: str | None,
    *,
    policy: TraceContextPolicy,
) -> str | None:
    """Apply the documented reject/normalize policy for traceparent."""
    if value is None:
        return None
    if policy == TraceContextPolicy.IGNORE:
        return None
    normalized = normalize_traceparent(value)
    if normalized is not None:
        return normalized
    if policy == TraceContextPolicy.NORMALIZE:
        return None
    raise EnvelopeValidationError("traceparent", "invalid W3C traceparent format")
