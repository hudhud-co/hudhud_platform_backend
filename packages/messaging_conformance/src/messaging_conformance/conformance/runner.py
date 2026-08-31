"""In-memory conformance runners for pure decision vectors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from messaging_conformance.conformance.assertions import (
    assert_jetstream_action,
    assert_no_handler_rerun,
    assert_publish_ack_transition,
    assert_sanitized_error_message,
)
from messaging_conformance.conformance.vectors import ConformanceVectorId
from messaging_conformance.enums import (
    InboxStatus,
    JetStreamConsumerAction,
    OutboxStatus,
    QuarantineRedeliveryPolicy,
)
from messaging_conformance.inbox_decisions import decide_inbox_duplicate_delivery
from messaging_conformance.outbox_decisions import (
    decide_outbox_publish_result,
    decide_stale_outbox_recovery,
)
from messaging_conformance.values import InboxRecordSnapshot, InboxUniqueKey, OutboxRecordSnapshot


def run_pure_decision_vector(vector_id: ConformanceVectorId) -> None:
    """Execute a pure decision conformance vector in memory."""
    if vector_id is ConformanceVectorId.C4:
        _run_c4_stale_lease_recovery()
        return
    if vector_id is ConformanceVectorId.C5:
        _run_c5_publish_ack_transition()
        return
    if vector_id is ConformanceVectorId.C6:
        _run_c6_processed_duplicate_no_rerun()
        return
    if vector_id is ConformanceVectorId.C7:
        _run_c7_processed_before_ack()
        return
    if vector_id is ConformanceVectorId.C8:
        _run_c8_quarantined_terminal_ack()
        return
    if vector_id is ConformanceVectorId.C10:
        _run_c10_error_sanitization_fixture()
        return
    msg = f"vector {vector_id} requires a PostgreSQL adapter"
    raise NotImplementedError(msg)


def _run_c4_stale_lease_recovery() -> None:
    now = datetime.now(tz=UTC)
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        processing_owner="relay-a",
        processing_until=now - timedelta(seconds=1),
        attempt_count=1,
        max_attempts=5,
    )
    decision = decide_stale_outbox_recovery(row, now=now)
    assert decision is not None
    assert decision.target_status is OutboxStatus.PENDING


def _run_c5_publish_ack_transition() -> None:
    row = OutboxRecordSnapshot(
        event_id=uuid4(),
        status=OutboxStatus.PROCESSING,
        attempt_count=1,
        max_attempts=5,
    )
    decision = decide_outbox_publish_result(row, broker_ack_received=True)
    assert_publish_ack_transition(decision, context="C5")


def _run_c6_processed_duplicate_no_rerun() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey(consumer_name="projection-a", event_id=uuid4()),
        status=InboxStatus.PROCESSED,
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert_no_handler_rerun(decision, context="C6")
    assert_jetstream_action(
        decision.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="C6",
    )


def _run_c7_processed_before_ack() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey(consumer_name="projection-a", event_id=uuid4()),
        status=InboxStatus.PROCESSED,
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert_jetstream_action(
        decision.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="C7",
    )


def _run_c8_quarantined_terminal_ack() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey(consumer_name="projection-a", event_id=uuid4()),
        status=InboxStatus.QUARANTINED,
    )
    decision = decide_inbox_duplicate_delivery(
        existing,
        now=now,
        quarantine_policy=QuarantineRedeliveryPolicy.ACK_TERMINAL,
    )
    assert_jetstream_action(
        decision.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="C8",
    )


def _run_c10_error_sanitization_fixture() -> None:
    assert_sanitized_error_message("NATS_TIMEOUT", context="C10")
    assert_sanitized_error_message("STALE_LEASE", context="C10")
