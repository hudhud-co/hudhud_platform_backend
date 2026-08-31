"""Retry classification primitives without fixed production timing."""

from __future__ import annotations

from messaging_conformance.enums import RetryClassification

TRANSIENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "NATS_TIMEOUT",
        "NATS_NO_RESPONDERS",
        "NATS_TEMPORARY",
        "DB_SERIALIZATION_FAILURE",
        "DB_DEADLOCK",
        "STALE_LEASE",
        "NETWORK_UNAVAILABLE",
    }
)

PERMANENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ENVELOPE_INVALID",
        "SUBJECT_FORBIDDEN",
        "PAYLOAD_TOO_LARGE",
        "SCHEMA_MISMATCH",
        "ACL_DENIED",
    }
)

POISON_ERROR_CODES: frozenset[str] = frozenset(
    {
        "HANDLER_POISON",
        "MAX_DELIVER_EXCEEDED",
        "DESERIALIZE_FAILURE",
    }
)


def classify_retry_error(error_code: str) -> RetryClassification:
    """Classify a sanitized error code for retry/quarantine decisions."""
    normalized = error_code.strip().upper()
    if normalized in POISON_ERROR_CODES:
        return RetryClassification.POISON
    if normalized in PERMANENT_ERROR_CODES:
        return RetryClassification.PERMANENT
    if normalized in TRANSIENT_ERROR_CODES:
        return RetryClassification.TRANSIENT
    return RetryClassification.TRANSIENT


def should_quarantine(
    *,
    classification: RetryClassification,
    attempt_count: int,
    max_attempts: int,
) -> bool:
    """Decide whether an outbox/inbox row should move to quarantined."""
    if classification is RetryClassification.POISON:
        return True
    if classification is RetryClassification.PERMANENT:
        return True
    return attempt_count >= max_attempts > 0
