"""A1 timeline consumer coordinator tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from conftest import SHIPMENT_ID, SOURCE_PK, a1_event_id
from messaging_conformance.conformance.assertions import (
    assert_inbox_terminal,
    assert_jetstream_action,
    assert_sanitized_error_message,
)
from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from tracking.application.coordinator import TimelineConsumerCoordinator
from tracking.domain.contract import A1_DURABLE_CONSUMER, A1_EVENT_TYPE, A1_STREAM, A1_SUBJECT
from tracking.domain.types import Delivery, InboxRow
from tracking.infrastructure.memory import MemoryTrackingStore, SimulatedCrash


def test_valid_a1_is_projected_and_acked_after_commit(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(outcome.jetstream_action, JetStreamConsumerAction.ACK, context="valid")
    assert outcome.inbox_status is not None
    assert_inbox_terminal(outcome.inbox_status, context="valid")
    assert outcome.timeline_written is True
    assert store.actions == ["commit", "ack"]
    entry = store.get_by_event_id(a1_event_id())
    assert entry is not None
    assert entry.source_pk == SOURCE_PK
    assert entry.shipment_id == SHIPMENT_ID
    assert entry.source_table == "shipment_events"
    inbox = store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id())
    assert inbox is not None
    assert inbox.status is InboxStatus.PROCESSED
    assert inbox.nats_msg_id is None


def test_wrong_producer_is_quarantined(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    outcome = coordinator.handle(make_delivery(make_envelope(producer="shipment")))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert outcome.timeline_written is False
    assert store.timeline_entry_count() == 0
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="wrong producer",
    )


def test_canonical_audit_fact_type_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    envelope = make_envelope(event_type="audit.fact.entry_recorded")
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.timeline_entry_count() == 0


def test_a2_event_type_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    outcome = coordinator.handle(
        make_delivery(make_envelope(event_type="legacy_bridge.observation.audit_entry"))
    )
    assert outcome.timeline_written is False
    assert store.timeline_entry_count() == 0
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_wrong_event_version_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    other_pk = uuid4()
    outcome = coordinator.handle(
        make_delivery(make_envelope(source_pk=other_pk, event_version=2))
    )
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.timeline_entry_count() == 0


def test_wrong_subject_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    outcome = coordinator.handle(
        make_delivery(subject="hudhud.audit.legacy_bridge.observation.audit_entry.v1")
    )
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.timeline_entry_count() == 0


def test_non_aggregate_enforced(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    envelope = make_envelope(
        aggregate_scope="aggregate",
        aggregate_type="shipment",
        aggregate_id=str(SHIPMENT_ID),
        aggregate_version=1,
    )
    outcome = coordinator.handle(make_delivery(envelope))
    assert store.timeline_entry_count() == 0
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_wrong_source_table_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    envelope = make_envelope(payload=make_payload(source_table="audit_logs"))
    outcome = coordinator.handle(make_delivery(envelope))
    assert store.timeline_entry_count() == 0
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_secret_like_metadata_is_rejected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    envelope = make_envelope(
        payload=make_payload(metadata={"jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb"})
    )
    outcome = coordinator.handle(make_delivery(envelope))
    assert store.timeline_entry_count() == 0
    inbox = store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id())
    assert inbox is not None
    assert inbox.status is InboxStatus.QUARANTINED
    assert inbox.last_error_message is not None
    assert_sanitized_error_message(inbox.last_error_message, context="secret metadata")
    assert "eyJ" not in inbox.last_error_message
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_no_projection_before_validation(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    store.fail_next_projection = True
    outcome = coordinator.handle(make_delivery(make_envelope(producer="shipment")))
    assert outcome.timeline_written is False
    assert store.timeline_entry_count() == 0
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_inbox_uniqueness_and_processed_duplicate_does_not_rerun(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    first = coordinator.handle(make_delivery(nats_msg_id="transport-1"))
    assert first.timeline_written is True
    second = coordinator.handle(make_delivery(nats_msg_id="transport-2"))
    assert second.timeline_written is False
    assert second.reason == "terminal_processed_duplicate"
    assert_jetstream_action(
        second.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="duplicate",
    )
    assert store.timeline_entry_count() == 1
    inbox = store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id())
    assert inbox is not None
    assert inbox.attempt_count == 1


def test_nats_msg_id_does_not_replace_inbox_uniqueness(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    coordinator.handle(make_delivery(nats_msg_id="first-broker-id"))
    coordinator.handle(make_delivery(nats_msg_id="second-broker-id"))
    assert store.timeline_entry_count() == 1


def test_commit_before_ack(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    coordinator.handle(make_delivery())
    assert store.actions[0] == "commit"
    assert store.actions[1] == "ack"


def test_crash_after_commit_redelivery_acks_without_double_projection(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.crash_before_ack = True
    with pytest.raises(SimulatedCrash):
        coordinator.handle(make_delivery())
    assert "commit" in store.actions
    assert "ack" not in store.actions
    assert store.timeline_entry_count() == 1
    inbox = store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id())
    assert inbox is not None
    assert inbox.status is InboxStatus.PROCESSED

    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="redelivery",
    )
    assert outcome.timeline_written is False
    assert store.timeline_entry_count() == 1
    assert store.actions[-1] == "ack"


def test_retryable_failure_rolls_back_and_naks(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.fail_next_projection = True
    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.NAK,
        context="rollback",
    )
    assert "rollback" in store.actions
    assert "commit" not in store.actions
    assert store.timeline_entry_count() == 0
    assert store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id()) is None


def test_active_lease_defers_without_second_effect(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    now: object,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=A1_DURABLE_CONSUMER,
            event_id=a1_event_id(),
            event_type=A1_EVENT_TYPE,
            event_version=1,
            status=InboxStatus.PROCESSING,
            processing_owner="other-replica",
            processing_lease_until=now + timedelta(seconds=20),  # type: ignore[operator]
            handler_version="0.1.0",
            attempt_count=1,
            first_received_at=now,  # type: ignore[arg-type]
            last_received_at=now,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream=A1_STREAM,
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.DEFER,
        context="active lease",
    )
    assert outcome.timeline_written is False
    assert store.timeline_entry_count() == 0
    assert store.actions[-1] == "defer"


def test_expired_lease_is_reclaimed_and_projected(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    now: object,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=A1_DURABLE_CONSUMER,
            event_id=a1_event_id(),
            event_type=A1_EVENT_TYPE,
            event_version=1,
            status=InboxStatus.PROCESSING,
            processing_owner="crashed-replica",
            processing_lease_until=now - timedelta(seconds=1),  # type: ignore[operator]
            handler_version="0.1.0",
            attempt_count=1,
            first_received_at=now,  # type: ignore[arg-type]
            last_received_at=now,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream=A1_STREAM,
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="expired lease",
    )
    assert store.timeline_entry_count() == 1
    inbox = store.committed_inbox(consumer_name=A1_DURABLE_CONSUMER, event_id=a1_event_id())
    assert inbox is not None
    assert inbox.status is InboxStatus.PROCESSED
    assert inbox.attempt_count == 2


def test_quarantine_on_max_attempts(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    now: object,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=A1_DURABLE_CONSUMER,
            event_id=a1_event_id(),
            event_type=A1_EVENT_TYPE,
            event_version=1,
            status=InboxStatus.FAILED,
            processing_owner=None,
            processing_lease_until=None,
            handler_version="0.1.0",
            attempt_count=5,
            first_received_at=now,  # type: ignore[arg-type]
            last_received_at=now,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=None,
            last_error_code="DB_DEADLOCK",
            last_error_message="retryable",
            jetstream_stream=A1_STREAM,
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.timeline_entry_count() == 0
    assert_jetstream_action(outcome.jetstream_action, JetStreamConsumerAction.ACK, context="poison")


def test_quarantined_redelivery_acks_without_projection(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    now: object,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=A1_DURABLE_CONSUMER,
            event_id=a1_event_id(),
            event_type=A1_EVENT_TYPE,
            event_version=1,
            status=InboxStatus.QUARANTINED,
            processing_owner=None,
            processing_lease_until=None,
            handler_version="0.1.0",
            attempt_count=5,
            first_received_at=now,  # type: ignore[arg-type]
            last_received_at=now,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=now,  # type: ignore[arg-type]
            last_error_code="SCHEMA_MISMATCH",
            last_error_message="permanent",
            jetstream_stream=A1_STREAM,
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert outcome.reason == "quarantined_terminal_duplicate"
    assert outcome.timeline_written is False
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="quarantine dup",
    )


def test_no_double_projection_on_processed_duplicate(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    first = coordinator.handle(make_delivery())
    assert first.timeline_written is True
    coordinator.handle(make_delivery())
    assert store.timeline_entry_count() == 1


def test_safe_error_storage_redacts_jwt(store: MemoryTrackingStore, now: object) -> None:
    store.begin()
    store.try_insert_received(
        consumer_name=A1_DURABLE_CONSUMER,
        event_id=a1_event_id(),
        event_type=A1_EVENT_TYPE,
        event_version=1,
        handler_version="0.1.0",
        processing_owner="test-worker",
        processing_lease_until=now + timedelta(seconds=30),  # type: ignore[operator]
        received_at=now,
        correlation_id=None,
        jetstream_stream=A1_STREAM,
        jetstream_seq=1,
        nats_msg_id=None,
    )
    row = store.mark_quarantined(
        consumer_name=A1_DURABLE_CONSUMER,
        event_id=a1_event_id(),
        quarantined_at=now,
        error_code="HANDLER_POISON",
        error_message="token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb leaked",
    )
    store.commit()
    assert row.last_error_message is not None
    assert_sanitized_error_message(row.last_error_message, context="stored error")
    assert "eyJ" not in row.last_error_message


def test_quarantine_persistence_failure_naks(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    store.fail_next_quarantine = True
    outcome = coordinator.handle(make_delivery(make_envelope(producer="shipment")))
    assert outcome.reason == "quarantine_persistence_failure_nak"
    assert outcome.jetstream_action is JetStreamConsumerAction.NAK
    assert store.actions[-1] == "nak"


def test_deserialize_poison_quarantines_with_evidence(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
) -> None:
    delivery = Delivery(
        body=b"not-json",
        subject=A1_SUBJECT,
        stream=A1_STREAM,
        consumer_name=A1_DURABLE_CONSUMER,
        jetstream_seq=99,
    )
    outcome = coordinator.handle(delivery)
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="poison json",
    )
    assert store.timeline_entry_count() == 0
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    rows = [
        row
        for row in store._inbox.values()
        if row.consumer_name == A1_DURABLE_CONSUMER
    ]
    assert len(rows) == 1
    assert rows[0].last_error_code == "DESERIALIZE_FAILURE"
    assert store.get_by_event_id(rows[0].event_id) is None


def test_synthetic_poison_id_not_in_projection(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
) -> None:
    delivery = Delivery(
        body=b"{broken",
        subject=A1_SUBJECT,
        stream=A1_STREAM,
        consumer_name=A1_DURABLE_CONSUMER,
        jetstream_seq=7,
    )
    coordinator.handle(delivery)
    assert store.timeline_entry_count() == 0
    rows = [row for row in store._inbox.values() if row.consumer_name == A1_DURABLE_CONSUMER]
    assert len(rows) == 1
    assert store.get_by_event_id(rows[0].event_id) is None
