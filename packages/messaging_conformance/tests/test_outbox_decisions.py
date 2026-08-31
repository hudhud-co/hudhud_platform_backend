"""Tests for outbox claim, recovery, and publish decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from messaging_conformance import (
    OutboxRecordSnapshot,
    OutboxStatus,
    RetryClassification,
    decide_outbox_publish_result,
    decide_stale_outbox_recovery,
    is_outbox_claimable,
    transport_msg_id_for_outbox,
)


def test_pending_row_is_claimable() -> None:
    now = datetime.now(tz=UTC)
    row = OutboxRecordSnapshot(event_id=uuid4(), status=OutboxStatus.PENDING, max_attempts=5)
    assert is_outbox_claimable(row, now=now)


def test_expired_processing_row_is_claimable() -> None:
    now = datetime.now(tz=UTC)
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        processing_until=now - timedelta(seconds=1),
        max_attempts=5,
    )
    assert is_outbox_claimable(row, now=now)


def test_active_processing_row_is_not_claimable() -> None:
    now = datetime.now(tz=UTC)
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        processing_until=now + timedelta(seconds=30),
        max_attempts=5,
    )
    assert not is_outbox_claimable(row, now=now)


def test_stale_lease_recovery_requeues_to_pending() -> None:
    now = datetime.now(tz=UTC)
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        processing_owner="relay-1",
        processing_until=now - timedelta(seconds=1),
        attempt_count=2,
        max_attempts=5,
    )
    decision = decide_stale_outbox_recovery(row, now=now)
    assert decision is not None
    assert decision.target_status is OutboxStatus.PENDING
    assert decision.clear_owner is True


def test_publish_ack_marks_published_and_allows_duplicate_publication() -> None:
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        max_attempts=5,
    )
    decision = decide_outbox_publish_result(row, broker_ack_received=True)
    assert decision.target_status is OutboxStatus.PUBLISHED
    assert decision.duplicate_publication_permitted is True


def test_transient_publish_failure_schedules_retry() -> None:
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        max_attempts=5,
    )
    decision = decide_outbox_publish_result(
        row,
        broker_ack_received=False,
        classification=RetryClassification.TRANSIENT,
    )
    assert decision.target_status is OutboxStatus.PENDING
    assert decision.schedule_retry is True


def test_permanent_publish_failure_quarantines() -> None:
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        max_attempts=5,
    )
    decision = decide_outbox_publish_result(
        row,
        broker_ack_received=False,
        classification=RetryClassification.PERMANENT,
    )
    assert decision.target_status is OutboxStatus.QUARANTINED


def test_transport_msg_id_uses_event_id() -> None:
    event_id = uuid4()
    row = OutboxRecordSnapshot(event_id=event_id, status=OutboxStatus.PENDING, max_attempts=5)
    assert transport_msg_id_for_outbox(row) == str(event_id)
