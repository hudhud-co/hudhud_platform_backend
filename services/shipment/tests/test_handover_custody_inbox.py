"""pickup.fact.handover_completed consumer: canonical custody release to a hub."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import DRIVER_ID, ORDER_ID, WAYBILL, seed_created_shipment
from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from shipment.application.accepted_fact_coordinator import (
    PickupAcceptedFactCoordinator,
    build_handover_fact_coordinator,
)
from shipment.application.handover_fact_apply import PickupHandoverCustodyApplyService
from shipment.domain.contract import (
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_SUBJECT,
    PICKUP_HANDOVER_DURABLE_CONSUMER,
    PICKUP_HANDOVER_EVENT_TYPE,
    PICKUP_HANDOVER_STREAM,
    PICKUP_HANDOVER_SUBJECT,
)
from shipment.domain.entities import Shipment
from shipment.domain.types import Delivery
from shipment.domain.value_objects import (
    CustodyType,
    HandoverOutcome,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.infrastructure.accepted_fact_memory import (
    MemoryAcceptedFactStore,
    RecordingTransport,
)

PICKUP_TASK_ID = UUID("f2faafa9-bca5-491f-b4b6-ef3b9884206c")
SHIPMENT_ID = UUID("835aa643-3538-4cb1-ae26-9beb65224618")
MANIFEST_ID = UUID("9c1f0b52-4c3a-4a0e-9c1e-8f2a6d5b4e10")
HUB_ID = UUID("6f9c1d23-0b55-4f9a-9d67-1c4e5a8b2d31")
EVENT_ID = UUID("5d2f1c9e-6a04-4f2b-8f0c-2ad9e1f4b7c3")
CORRELATION_ID = UUID("3a7e43e5-0014-4a89-abe5-5e927cf3a3e9")
RELEASED_AT = "2026-09-04T15:30:00.000Z"
HUB_OPERATOR = "hub-operator-7"
NOW = datetime(2026, 9, 4, 15, 30, 5, tzinfo=UTC)

PHOTO_REF = {
    "ref_type": "hub_condition_photo",
    "bucket": "hudhud-evidence",
    "key": "pickup/handover/condition-1.jpg",
    "content_type": "image/jpeg",
}


def handover_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pickup_task_id": str(PICKUP_TASK_ID),
        "shipment_id": str(SHIPMENT_ID),
        "handover_manifest_id": str(MANIFEST_ID),
        "receiving_hub_id": str(HUB_ID),
        "outcome": "RECEIVED",
        "released_at": RELEASED_AT,
        "releasing_driver_user_id": DRIVER_ID,
        "receiving_actor_id": HUB_OPERATOR,
    }
    payload.update(overrides)
    return payload


def handover_envelope(**overrides: Any) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "event_id": str(EVENT_ID),
        "event_type": PICKUP_HANDOVER_EVENT_TYPE,
        "event_version": 1,
        "occurred_at": RELEASED_AT,
        "producer": "pickup",
        "message_kind": "integration",
        "aggregate_scope": "aggregate",
        "aggregate_type": "pickup_task",
        "aggregate_id": str(PICKUP_TASK_ID),
        "aggregate_version": 4,
        "correlation_id": str(CORRELATION_ID),
        "data_classification": "internal",
        "pii_present": False,
        "schema_uri": (
            "https://hudhud.platform/contracts/events/"
            "pickup.fact.handover_completed/v1.schema.json"
        ),
        "payload": handover_payload(),
    }
    if "payload" in overrides:
        envelope["payload"] = overrides.pop("payload")
    envelope.update(overrides)
    return envelope


def handover_delivery(
    envelope: dict[str, Any] | None = None,
    *,
    subject: str = PICKUP_HANDOVER_SUBJECT,
    stream: str = PICKUP_HANDOVER_STREAM,
    consumer_name: str = PICKUP_HANDOVER_DURABLE_CONSUMER,
    jetstream_seq: int | None = 11,
) -> Delivery:
    return Delivery(
        body=json.dumps(envelope or handover_envelope()).encode("utf-8"),
        subject=subject,
        stream=stream,
        consumer_name=consumer_name,
        jetstream_seq=jetstream_seq,
    )


def seed_in_driver_custody(
    store: MemoryAcceptedFactStore,
    *,
    custody_id: str = DRIVER_ID,
) -> Shipment:
    shipment = Shipment(
        shipment_id=SHIPMENT_ID,
        order_id=ORDER_ID,
        waybill_identity=WaybillIdentity(
            waybill_number=WAYBILL, shipment_id=str(SHIPMENT_ID)
        ),
        current_status=ShipmentStatus.IN_CUSTODY,
        order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        accepted_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        sla_started_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        current_custody_type=CustodyType.PICKUP_DRIVER,
        current_custody_id=custody_id,
        version=2,
    )
    store.seed_shipment(shipment)
    return shipment


@pytest.fixture
def handover_store() -> MemoryAcceptedFactStore:
    store = MemoryAcceptedFactStore()
    seed_in_driver_custody(store)
    return store


@pytest.fixture
def handover_coordinator(
    handover_store: MemoryAcceptedFactStore,
) -> PickupAcceptedFactCoordinator:
    return build_handover_fact_coordinator(
        unit_of_work=handover_store,
        inbox=handover_store,
        transport=RecordingTransport(handover_store),
        apply_service=PickupHandoverCustodyApplyService(handover_store),
        consumer_name=PICKUP_HANDOVER_DURABLE_CONSUMER,
        handler_version="test-handler",
        processing_owner="test-owner",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: NOW,
    )


# ---------------------------------------------------------------------- apply


def test_a_clean_receipt_moves_custody_to_the_receiving_hub(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    outcome = handover_coordinator.handle(handover_delivery())
    assert outcome.jetstream_action is JetStreamConsumerAction.ACK
    assert outcome.inbox_status is InboxStatus.PROCESSED
    assert handover_store.actions == ["begin", "commit", "ack"]

    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_custody_type is CustodyType.ORIGIN_HUB
    assert shipment.current_custody_id == str(HUB_ID)
    assert shipment.custody_transferred_at is not None
    # Physical delivery progress is irreversible: the shipment stays IN_CUSTODY.
    assert shipment.current_status is ShipmentStatus.IN_CUSTODY


def test_the_transfer_and_its_audit_trail_are_recorded(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    handover_coordinator.handle(handover_delivery())
    transfer = handover_store.custody_transfers.get_for_pickup_task(PICKUP_TASK_ID)
    assert transfer is not None
    assert transfer.from_custody_type is CustodyType.PICKUP_DRIVER
    assert transfer.from_custody_id == DRIVER_ID
    assert transfer.to_custody_type is CustodyType.ORIGIN_HUB
    assert transfer.outcome is HandoverOutcome.RECEIVED

    audits = handover_store.audit_logs.list_entries_for_entity("shipment", str(SHIPMENT_ID))
    assert [entry.action for entry in audits] == ["SHIPMENT_HUB_HANDOVER_RECEIPT"]
    assert audits[0].actor_id == HUB_OPERATOR

    events = handover_store.shipment_events.list_events_for_shipment(SHIPMENT_ID)
    assert [event.event_type for event in events] == [
        ShipmentEventType.HUB_HANDOVER_RECEIPT
    ]


def test_a_disputed_receipt_still_releases_custody(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(
            outcome="RECEIVED_WITH_DISCREPANCY",
            discrepancy_reason="DAMAGED_AT_HANDOVER",
        ),
        media_refs=[PHOTO_REF],
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.PROCESSED
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_custody_type is CustodyType.ORIGIN_HUB
    transfer = handover_store.custody_transfers.get_for_pickup_task(PICKUP_TASK_ID)
    assert transfer is not None
    assert transfer.discrepancy_reason == "DAMAGED_AT_HANDOVER"
    assert transfer.condition_evidence


def test_receiving_at_an_unplanned_hub_moves_custody_to_the_actual_hub(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    actual_hub = uuid4()
    envelope = handover_envelope(
        payload=handover_payload(
            outcome="RECEIVED_WITH_DISCREPANCY",
            discrepancy_reason="WRONG_HUB_RECEIVED",
            receiving_hub_id=str(actual_hub),
        ),
        media_refs=[PHOTO_REF],
    )
    handover_coordinator.handle(handover_delivery(envelope))
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment is not None
    assert shipment.current_custody_id == str(actual_hub)


# ------------------------------------------------------------ at-least-once


def test_a_redelivered_fact_converges_without_transferring_twice(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    delivery = handover_delivery()
    first = handover_coordinator.handle(delivery)
    version_after_first = handover_store.shipments.get_shipment(SHIPMENT_ID).version
    second = handover_coordinator.handle(delivery)
    assert first.inbox_status is InboxStatus.PROCESSED
    assert second.jetstream_action is JetStreamConsumerAction.ACK
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment.version == version_after_first
    transfers = handover_store.custody_transfers.list_for_shipment(SHIPMENT_ID)
    assert len(transfers) == 1


def test_a_conflicting_transfer_for_the_same_task_is_quarantined(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    handover_coordinator.handle(handover_delivery())
    conflicting = handover_envelope(
        event_id=str(uuid4()),
        aggregate_version=5,
        payload=handover_payload(handover_manifest_id=str(uuid4())),
    )
    outcome = handover_coordinator.handle(handover_delivery(conflicting))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert outcome.jetstream_action is JetStreamConsumerAction.ACK
    assert len(handover_store.custody_transfers.list_for_shipment(SHIPMENT_ID)) == 1


# ------------------------------------------------------------- fail closed


def test_a_release_by_someone_who_does_not_hold_custody_is_quarantined(
    handover_store: MemoryAcceptedFactStore,
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    seed_in_driver_custody(handover_store, custody_id="driver-someone-else")
    outcome = handover_coordinator.handle(handover_delivery())
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment.current_custody_type is CustodyType.PICKUP_DRIVER


def test_a_release_before_custody_started_is_quarantined(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    store = MemoryAcceptedFactStore()
    seed_created_shipment(store, shipment_id=SHIPMENT_ID)
    coordinator = build_handover_fact_coordinator(
        unit_of_work=store,
        inbox=store,
        transport=RecordingTransport(store),
        apply_service=PickupHandoverCustodyApplyService(store),
        consumer_name=PICKUP_HANDOVER_DURABLE_CONSUMER,
        handler_version="test-handler",
        processing_owner="test-owner",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: NOW,
    )
    outcome = coordinator.handle(handover_delivery())
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert store.custody_transfers.get_for_pickup_task(PICKUP_TASK_ID) is None


def test_a_driver_recording_its_own_receipt_is_rejected(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(receiving_actor_id=DRIVER_ID)
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment.current_custody_type is CustodyType.PICKUP_DRIVER


def test_a_missing_parcel_outcome_is_rejected(
    handover_coordinator: PickupAcceptedFactCoordinator,
    handover_store: MemoryAcceptedFactStore,
) -> None:
    envelope = handover_envelope(payload=handover_payload(outcome="MISSING"))
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    shipment = handover_store.shipments.get_shipment(SHIPMENT_ID)
    assert shipment.current_custody_type is CustodyType.PICKUP_DRIVER


def test_missing_from_driver_is_rejected_as_a_releasing_reason(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(
            outcome="RECEIVED_WITH_DISCREPANCY",
            discrepancy_reason="MISSING_FROM_DRIVER",
        ),
        media_refs=[PHOTO_REF],
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_a_dispute_without_evidence_is_rejected(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(
            outcome="RECEIVED_WITH_DISCREPANCY",
            discrepancy_reason="DAMAGED_AT_HANDOVER",
        )
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_a_clean_receipt_carrying_a_discrepancy_reason_is_rejected(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(discrepancy_reason="DAMAGED_AT_HANDOVER"),
        media_refs=[PHOTO_REF],
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_the_acceptance_subject_is_refused_on_the_handover_consumer(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    outcome = handover_coordinator.handle(
        handover_delivery(subject=PICKUP_ACCEPTED_SUBJECT)
    )
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_a_foreign_durable_consumer_is_refused(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    outcome = handover_coordinator.handle(
        handover_delivery(consumer_name="shipment_pickup_facts_v1")
    )
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_a_shipment_aggregate_claim_is_refused(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    envelope = handover_envelope(
        aggregate_type="shipment", aggregate_id=str(SHIPMENT_ID)
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_a_payload_claiming_a_shipment_version_is_refused(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    envelope = handover_envelope(
        payload=handover_payload(shipment_aggregate_version=9)
    )
    outcome = handover_coordinator.handle(handover_delivery(envelope))
    assert outcome.inbox_status is InboxStatus.QUARANTINED


def test_an_undeserializable_body_is_quarantined_not_retried_forever(
    handover_coordinator: PickupAcceptedFactCoordinator,
) -> None:
    delivery = Delivery(
        body=b"not-json",
        subject=PICKUP_HANDOVER_SUBJECT,
        stream=PICKUP_HANDOVER_STREAM,
        consumer_name=PICKUP_HANDOVER_DURABLE_CONSUMER,
        jetstream_seq=12,
    )
    outcome = handover_coordinator.handle(delivery)
    assert outcome.inbox_status is InboxStatus.QUARANTINED
    assert outcome.jetstream_action is JetStreamConsumerAction.ACK


def test_the_two_pickup_consumers_have_distinct_durable_names() -> None:
    assert PICKUP_ACCEPTED_DURABLE_CONSUMER != PICKUP_HANDOVER_DURABLE_CONSUMER
    assert PICKUP_HANDOVER_DURABLE_CONSUMER == "shipment_pickup_handover_facts_v1"
