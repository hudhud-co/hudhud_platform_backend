"""pickup.fact.accepted durable inbox consumer tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import (
    DRIVER_ID,
    EVENT_ID,
    PICKUP_TASK_ID,
    SHIPMENT_ID,
    WAYBILL,
    valid_envelope,
    valid_payload,
)
from messaging_conformance.conformance.assertions import (
    assert_inbox_terminal,
    assert_jetstream_action,
    assert_sanitized_error_message,
)
from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.domain.contract import PICKUP_ACCEPTED_DURABLE_CONSUMER
from shipment.domain.entities import AcceptanceDecisionRecord, Shipment
from shipment.domain.types import Delivery, InboxRow
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.infrastructure.accepted_fact_memory import (
    MemoryAcceptedFactStore,
    SimulatedCrash,
)


def test_valid_accepted_atomic_apply(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    outcome = coordinator.handle(make_delivery())
    assert_jetstream_action(outcome.jetstream_action, JetStreamConsumerAction.ACK, context="valid")
    assert outcome.acceptance_applied is True
    assert outcome.inbox_status is InboxStatus.PROCESSED
    assert_inbox_terminal(outcome.inbox_status, context="valid")
    assert store.actions == ["begin", "commit", "ack"]

    shipment = store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_status is ShipmentStatus.IN_CUSTODY
    assert shipment.current_custody_type is CustodyType.PICKUP_DRIVER
    assert shipment.current_custody_id == DRIVER_ID
    assert shipment.accepted_at is not None
    assert shipment.sla_started_at == shipment.accepted_at
    assert shipment.version == 2
    assert outcome.shipment_version == 2

    events = store.shipment_events.list_events_for_shipment(SHIPMENT_ID)
    assert len(events) == 1
    assert events[0].event_type is ShipmentEventType.ACCEPTANCE_SCAN

    decision = store.acceptance_decisions.get_for_shipment(SHIPMENT_ID)
    assert decision is not None
    assert decision.outcome is AcceptanceOutcome.ACCEPTED
    assert decision.acting_driver_user_id == DRIVER_ID
    assert decision.scanned_identifier == WAYBILL
    assert decision.pickup_task_id == PICKUP_TASK_ID

    audits = store.audit_logs.list_entries_for_entity("shipment", str(SHIPMENT_ID))
    assert len(audits) == 1
    assert audits[0].actor_id == DRIVER_ID


def test_accepted_with_exception_requires_media_refs(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = valid_envelope(
        event_id="b2fc03ac-4e8e-5b9d-cf3f-cdd908abf685",
        aggregate_version=4,
        payload=valid_payload(outcome="ACCEPTED_WITH_EXCEPTION"),
        media_refs=[
            {
                "ref_type": "s3",
                "bucket": "hudhud-evidence",
                "key": "pickup-evidence/exception-note.jpg",
                "content_type": "image/jpeg",
            }
        ],
    )
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.acceptance_applied is True
    decision = store.acceptance_decisions.get_for_shipment(SHIPMENT_ID)
    assert decision is not None
    assert decision.outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION
    assert len(decision.exception_evidence) == 1
    assert "hudhud-evidence" in decision.exception_evidence[0].storage_uri


def test_exception_without_media_refs_quarantines(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = valid_envelope(payload=valid_payload(outcome="ACCEPTED_WITH_EXCEPTION"))
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.acceptance_applied is False
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.shipments.get_shipment(SHIPMENT_ID).current_status is ShipmentStatus.CREATED  # type: ignore[union-attr]
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None


@pytest.mark.parametrize(
    ("overrides", "kwargs"),
    [
        ({"producer": "shipment"}, {}),
        ({"event_type": "pickup.fact.rejected"}, {}),
        ({"event_version": 2}, {}),
        ({"aggregate_type": "shipment"}, {}),
        ({"aggregate_version": 0}, {}),
        ({"payload": valid_payload(outcome="REJECTED")}, {}),
        ({}, {"subject": "hudhud.pickup.pickup.fact.other.v1"}),
        (
            {"aggregate_id": str(uuid4()), "payload": valid_payload()},
            {},
        ),
    ],
)
def test_contract_envelope_validation_rejects(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
    overrides: dict[str, Any],
    kwargs: dict[str, Any],
) -> None:
    envelope = valid_envelope(**overrides)
    if "aggregate_id" in overrides and "payload" not in overrides:
        # mismatched aggregate covered by separate case
        pass
    outcome = coordinator.handle(make_delivery(envelope, **kwargs))
    assert outcome.acceptance_applied is False
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None
    shipment = store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_status is ShipmentStatus.CREATED


def test_scanned_identifier_mismatch_quarantines(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = valid_envelope(payload=valid_payload(scanned_identifier="WRONG-WB"))
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert outcome.acceptance_applied is False
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None


def test_no_pickup_task_snapshot_dependency(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    # MemoryAcceptedFactStore has no pickup_tasks collection at all.
    assert not hasattr(store, "pickup_tasks")
    outcome = coordinator.handle(make_delivery())
    assert outcome.acceptance_applied is True
    assert store.pickup_task_reads == 0


def test_commit_before_ack(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    coordinator.handle(make_delivery())
    assert store.actions[-2:] == ["commit", "ack"]
    assert store.actions.index("commit") < store.actions.index("ack")


def test_processed_duplicate_acks_without_reapply(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    first = coordinator.handle(make_delivery())
    assert first.acceptance_applied is True
    second = coordinator.handle(make_delivery())
    assert second.acceptance_applied is False
    assert second.reason == "terminal_processed_duplicate"
    assert_jetstream_action(second.jetstream_action, JetStreamConsumerAction.ACK, context="dup")
    events = store.shipment_events.list_events_for_shipment(SHIPMENT_ID)
    assert len(events) == 1


def test_active_lease_defers(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
    now: datetime,
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=EVENT_ID,
            event_type="pickup.fact.accepted",
            event_version=1,
            status=InboxStatus.PROCESSING,
            processing_owner="other-replica",
            processing_lease_until=now + timedelta(seconds=30),
            handler_version="test",
            attempt_count=1,
            first_received_at=now,
            last_received_at=now,
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream="HUDHUD_PICKUP",
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
            processing_started_at=now,
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert outcome.jetstream_action is JetStreamConsumerAction.DEFER
    assert outcome.acceptance_applied is False
    assert store.shipments.get_shipment(SHIPMENT_ID).current_status is ShipmentStatus.CREATED  # type: ignore[union-attr]


def test_expired_lease_reclaims_and_applies(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
    now: datetime,
) -> None:
    store.seed_inbox(
        InboxRow(
            id=uuid4(),
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=EVENT_ID,
            event_type="pickup.fact.accepted",
            event_version=1,
            status=InboxStatus.PROCESSING,
            processing_owner="stale-replica",
            processing_lease_until=now - timedelta(seconds=1),
            handler_version="test",
            attempt_count=1,
            first_received_at=now - timedelta(minutes=1),
            last_received_at=now - timedelta(minutes=1),
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream="HUDHUD_PICKUP",
            jetstream_seq=1,
            correlation_id=None,
            nats_msg_id=None,
            processing_started_at=now - timedelta(minutes=1),
        )
    )
    outcome = coordinator.handle(make_delivery())
    assert outcome.acceptance_applied is True
    assert outcome.reason == "reclaimed_processed_ack_after_commit"
    assert store.shipments.get_shipment(SHIPMENT_ID).current_status is ShipmentStatus.IN_CUSTODY  # type: ignore[union-attr]


def test_retryable_rollback_naks(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.fail_next_apply = True
    outcome = coordinator.handle(make_delivery())
    assert outcome.jetstream_action is JetStreamConsumerAction.NAK
    assert outcome.acceptance_applied is False
    assert "rollback" in store.actions
    assert "ack" not in store.actions
    shipment = store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_status is ShipmentStatus.CREATED
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None
    assert store.shipment_events.list_events_for_shipment(SHIPMENT_ID) == ()
    assert store.audit_logs.list_entries_for_entity("shipment", str(SHIPMENT_ID)) == ()


def test_durable_quarantine_then_ack(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    outcome = coordinator.handle(make_delivery(valid_envelope(producer="hub")))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert_jetstream_action(outcome.jetstream_action, JetStreamConsumerAction.ACK, context="q")
    inbox = store.committed_inbox(consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER, event_id=EVENT_ID)
    assert inbox is not None
    assert inbox.last_error_code == "SCHEMA_MISMATCH"
    assert_sanitized_error_message(inbox.last_error_message or "", context="quarantine")


def test_quarantine_persistence_failure_naks(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.fail_next_quarantine = True
    outcome = coordinator.handle(make_delivery(valid_envelope(producer="hub")))
    assert outcome.jetstream_action is JetStreamConsumerAction.NAK
    assert outcome.reason == "quarantine_persistence_failure_nak"


def test_compatibility_http_acceptance_conflict_quarantines(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_decision(
        AcceptanceDecisionRecord(
            decision_id=uuid4(),
            shipment_id=SHIPMENT_ID,
            pickup_task_id=uuid4(),
            outcome=AcceptanceOutcome.ACCEPTED,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=WAYBILL,
            scan_timestamp=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
            recorded_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        )
    )
    shipment = store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    shipment.current_status = ShipmentStatus.IN_CUSTODY
    shipment.accepted_at = datetime(2026, 9, 4, 11, 0, tzinfo=UTC)
    shipment.sla_started_at = shipment.accepted_at
    shipment.current_custody_type = CustodyType.PICKUP_DRIVER
    shipment.current_custody_id = DRIVER_ID
    store.seed_shipment(shipment)

    outcome = coordinator.handle(make_delivery())
    assert outcome.acceptance_applied is False
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    inbox = store.committed_inbox(consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER, event_id=EVENT_ID)
    assert inbox is not None
    assert inbox.last_error_code == "ACCEPTANCE_CONFLICT"
    assert inbox.status is InboxStatus.QUARANTINED
    # Not silently treated as this fact's successful processed delivery.
    assert outcome.reason == "permanent_quarantine_ack"
    events = store.shipment_events.list_events_for_shipment(SHIPMENT_ID)
    assert events == ()


def test_crash_after_commit_before_ack_redelivery(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.crash_before_ack = True
    with pytest.raises(SimulatedCrash):
        coordinator.handle(make_delivery())
    assert "commit" in store.actions
    assert "ack" not in store.actions
    assert store.shipments.get_shipment(SHIPMENT_ID).current_status is ShipmentStatus.IN_CUSTODY  # type: ignore[union-attr]

    outcome = coordinator.handle(make_delivery())
    assert outcome.acceptance_applied is False
    assert_jetstream_action(
        outcome.jetstream_action,
        JetStreamConsumerAction.ACK,
        context="redeliver",
    )
    events = store.shipment_events.list_events_for_shipment(SHIPMENT_ID)
    assert len(events) == 1


def test_independent_shipment_version_increment(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    store.seed_shipment(
        Shipment(
            shipment_id=SHIPMENT_ID,
            order_id=UUID("11111111-2222-4333-8444-555555555555"),
            waybill_identity=WaybillIdentity(
                waybill_number=WAYBILL,
                shipment_id=str(SHIPMENT_ID),
            ),
            current_status=ShipmentStatus.CREATED,
            order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
            version=7,
        )
    )
    envelope = valid_envelope(aggregate_version=99)
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.acceptance_applied is True
    shipment = store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.version == 8
    assert outcome.shipment_version == 8
    # PickupTask aggregate_version must not become Shipment version.
    assert shipment.version != 99


def test_synthetic_poison_identity_never_enters_domain(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
) -> None:
    delivery = Delivery(
        body=b"not-json{{{",
        subject="hudhud.pickup.pickup.fact.accepted.v1",
        stream="HUDHUD_PICKUP",
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        jetstream_seq=99,
    )
    outcome = coordinator.handle(delivery)
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None
    assert store.shipment_events.list_events_for_shipment(SHIPMENT_ID) == ()
    assert store._shipment_events == []
    assert store._decisions == {}
    assert store._audit_logs == []


def test_forbidden_inline_evidence_rejected(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = valid_envelope(payload=valid_payload(inline_evidence="base64blob"))
    outcome = coordinator.handle(make_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.acceptance_decisions.get_for_shipment(SHIPMENT_ID) is None


def test_wrong_consumer_name_rejected(
    coordinator: PickupAcceptedFactCoordinator,
    store: MemoryAcceptedFactStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    outcome = coordinator.handle(make_delivery(consumer_name="other_consumer"))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
