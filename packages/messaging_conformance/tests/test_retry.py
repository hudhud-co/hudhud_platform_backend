"""Tests for retry classification."""

from __future__ import annotations

from messaging_conformance import (
    RetryClassification,
    classify_retry_error,
    should_quarantine,
)


def test_classify_transient_errors() -> None:
    assert classify_retry_error("NATS_TIMEOUT") is RetryClassification.TRANSIENT
    assert classify_retry_error("stale_lease") is RetryClassification.TRANSIENT


def test_classify_permanent_errors() -> None:
    assert classify_retry_error("ACL_DENIED") is RetryClassification.PERMANENT


def test_classify_poison_errors() -> None:
    assert classify_retry_error("HANDLER_POISON") is RetryClassification.POISON


def test_should_quarantine_on_poison_or_max_attempts() -> None:
    assert should_quarantine(
        classification=RetryClassification.POISON,
        attempt_count=1,
        max_attempts=5,
    )
    assert should_quarantine(
        classification=RetryClassification.TRANSIENT,
        attempt_count=5,
        max_attempts=5,
    )
    assert not should_quarantine(
        classification=RetryClassification.TRANSIENT,
        attempt_count=2,
        max_attempts=5,
    )
