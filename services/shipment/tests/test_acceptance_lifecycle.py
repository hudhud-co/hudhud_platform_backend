"""Acceptance lifecycle invariant tests — Phase 11 prerequisites and atomic effects."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RecordAcceptanceScanCommand,
    RegisterPickupTaskCommand,
)
from shipment.domain.entities import PickupTaskSnapshot
from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ActingDriverNotAssigned,
    ExceptionEvidenceRequired,
    InlineMediaNotAllowed,
    PickupConditionProofMissing,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotProofCaptured,
    ScannedIdentifierMismatch,
    ShipmentNotCreated,
)
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    EvidenceReference,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentEventType,
    ShipmentStatus,
)
from shipment.infrastructure.memory import InMemoryAcceptanceUnitOfWork, SimulatedCommitFailure


def _service() -> tuple[AcceptanceLifecycleService, InMemoryAcceptanceUnitOfWork]:
    store = InMemoryAcceptanceUnitOfWork()
    return AcceptanceLifecycleService(store), store


def _scan_time() -> datetime:
    return datetime(2026, 5, 31, 10, 0, tzinfo=UTC)


def _created_at() -> datetime:
    return datetime(2026, 5, 31, 9, 0, tzinfo=UTC)


def _evidence(
    uri: str = "s3://proof-bucket/parcel-photo-001.jpg",
    *,
    captured_at: datetime | None = None,
) -> EvidenceReference:
    return EvidenceReference.from_reference(
        uri,
        captured_at=captured_at or _scan_time(),
        location_label="pickup-point-alpha",
    )


def _shipment_status(store: InMemoryAcceptanceUnitOfWork, shipment_id: UUID) -> ShipmentStatus:
    shipment = store.shipments.get_shipment(shipment_id)
    assert shipment is not None
    return shipment.current_status


def _seed_ready_acceptance(
    service: AcceptanceLifecycleService,
    *,
    waybill_number: str = "WB-1001",
    driver_id: str = "driver-42",
) -> tuple[object, object, PickupTaskSnapshot]:
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number=waybill_number,
            created_at=_created_at(),
        )
    )
    pickup_task_id = uuid4()
    batch_id = uuid4()
    pickup_task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=batch_id,
            has_pickup_condition_proof=True,
        )
    )
    return shipment, pickup_task_id, pickup_task


def test_new_order_has_no_custody_or_sla_start() -> None:
    service, _store = _service()
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-1001",
            created_at=_created_at(),
        )
    )

    assert shipment.current_status is ShipmentStatus.CREATED
    assert shipment.custody_active is False
    assert shipment.sla_active is False
    assert shipment.accepted_at is None
    assert shipment.current_custody_type is None


def test_successful_acceptance_produces_all_atomic_effects() -> None:
    service, store = _service()
    shipment, pickup_task_id, _pickup = _seed_ready_acceptance(service)
    scan_timestamp = _scan_time()
    driver_id = "driver-42"

    result = service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id=driver_id,
            scanned_identifier="WB-1001",
            scan_timestamp=scan_timestamp,
            outcome=AcceptanceOutcome.ACCEPTED,
            recorded_at=scan_timestamp,
        )
    )

    updated = store.shipments.get_shipment(shipment.shipment_id)
    updated_pickup = store.pickup_tasks.get_pickup_task(pickup_task_id)
    events = store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    audit_entries = store.audit_logs.list_entries_for_entity(
        "shipment", str(shipment.shipment_id)
    )

    assert updated is not None
    assert updated.current_status is ShipmentStatus.IN_CUSTODY
    assert updated.accepted_at == scan_timestamp
    assert updated.sla_started_at == scan_timestamp
    assert updated.current_custody_type is CustodyType.DRIVER
    assert updated.current_custody_id == driver_id
    assert updated_pickup is not None
    assert updated_pickup.acceptance_state is PickupTaskAcceptanceState.ACCEPTED
    assert len(events) == 1
    assert events[0].event_type is ShipmentEventType.ACCEPTANCE_SCAN
    assert events[0].previous_status is ShipmentStatus.CREATED
    assert events[0].new_status is ShipmentStatus.IN_CUSTODY
    assert len(audit_entries) == 1
    assert result.shipment_event is not None
    assert result.audit_log.action == "SHIPMENT_ACCEPTANCE_SCAN"
    assert "commit" in store.actions


def test_custody_and_sla_start_at_same_scan_timestamp() -> None:
    service, store = _service()
    shipment, pickup_task_id, _pickup = _seed_ready_acceptance(service, waybill_number="WB-2001")
    scan_timestamp = _scan_time()

    service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id="driver-42",
            scanned_identifier="WB-2001",
            scan_timestamp=scan_timestamp,
            outcome=AcceptanceOutcome.ACCEPTED,
        )
    )

    updated = store.shipments.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.accepted_at == scan_timestamp
    assert updated.sla_started_at == scan_timestamp


@pytest.mark.parametrize(
    ("mutator", "expected_error"),
    [
        (
            lambda task: PickupTaskSnapshot(
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                status=PickupTaskStatus.PENDING,
                assigned_driver_user_id=task.assigned_driver_user_id,
                assigned_batch_id=task.assigned_batch_id,
                has_pickup_condition_proof=task.has_pickup_condition_proof,
            ),
            PickupTaskNotProofCaptured,
        ),
        (
            lambda task: PickupTaskSnapshot(
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                status=task.status,
                assigned_driver_user_id=None,
                assigned_batch_id=task.assigned_batch_id,
                has_pickup_condition_proof=task.has_pickup_condition_proof,
            ),
            PickupTaskMissingAssignedDriver,
        ),
        (
            lambda task: PickupTaskSnapshot(
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                status=task.status,
                assigned_driver_user_id=task.assigned_driver_user_id,
                assigned_batch_id=None,
                has_pickup_condition_proof=task.has_pickup_condition_proof,
            ),
            PickupTaskMissingAssignedBatch,
        ),
        (
            lambda task: task,
            ActingDriverNotAssigned,
        ),
        (
            lambda task: PickupTaskSnapshot(
                pickup_task_id=task.pickup_task_id,
                shipment_id=task.shipment_id,
                status=task.status,
                assigned_driver_user_id=task.assigned_driver_user_id,
                assigned_batch_id=task.assigned_batch_id,
                has_pickup_condition_proof=False,
            ),
            PickupConditionProofMissing,
        ),
    ],
)
def test_prerequisite_violations_raise_explicit_errors(
    mutator,
    expected_error,
) -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)
    store.pickup_tasks.save_pickup_task(mutator(pickup_task))

    acting_driver = (
        "wrong-driver"
        if expected_error is ActingDriverNotAssigned
        else pickup_task.assigned_driver_user_id or "driver-42"
    )

    with pytest.raises(expected_error):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=acting_driver,
                scanned_identifier="WB-1001",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED,
            )
        )

    assert _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED
    assert not store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert not store.audit_logs.list_entries_for_entity("shipment", str(shipment.shipment_id))


def test_shipment_not_created_rejects_acceptance() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)
    shipment.current_status = ShipmentStatus.IN_CUSTODY
    store.shipments.save_shipment(shipment)

    with pytest.raises(ShipmentNotCreated):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-1001",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED,
            )
        )


def test_mismatched_scanned_identifier_rejects_acceptance() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)

    with pytest.raises(ScannedIdentifierMismatch):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-9999",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED,
            )
        )

    assert not store.shipment_events.list_events_for_shipment(shipment.shipment_id)


def test_repeated_acceptance_cannot_overwrite() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)
    scan_timestamp = _scan_time()
    command = RecordAcceptanceScanCommand(
        shipment_id=shipment.shipment_id,
        pickup_task_id=pickup_task_id,
        acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
        scanned_identifier="WB-1001",
        scan_timestamp=scan_timestamp,
        outcome=AcceptanceOutcome.ACCEPTED,
    )
    service.record_acceptance_scan(command)

    with pytest.raises(AcceptanceAlreadyRecorded):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-1001",
                scan_timestamp=scan_timestamp,
                outcome=AcceptanceOutcome.REJECTED,
            )
        )

    events = store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert len(events) == 1
    assert events[0].event_type is ShipmentEventType.ACCEPTANCE_SCAN


def test_rollback_leaves_all_stores_unchanged_on_commit_failure() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)
    store.fail_on_commit = True

    with pytest.raises(SimulatedCommitFailure):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-1001",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED,
            )
        )

    assert _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED
    assert store.pickup_tasks.get_pickup_task(pickup_task_id).acceptance_state is None
    assert not store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert not store.audit_logs.list_entries_for_entity("shipment", str(shipment.shipment_id))
    assert "rollback" in store.actions


def test_rejected_outcome_does_not_start_custody_sla_or_acceptance_scan_event() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(
        service, waybill_number="WB-4001"
    )

    service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier="WB-4001",
            scan_timestamp=_scan_time(),
            outcome=AcceptanceOutcome.REJECTED,
        )
    )

    updated = store.shipments.get_shipment(shipment.shipment_id)
    updated_pickup = store.pickup_tasks.get_pickup_task(pickup_task_id)
    assert updated is not None
    assert updated.current_status is ShipmentStatus.CREATED
    assert updated.accepted_at is None
    assert updated.sla_started_at is None
    assert updated.current_custody_type is None
    assert updated_pickup is not None
    assert updated_pickup.acceptance_state is PickupTaskAcceptanceState.REJECTED
    assert not store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert len(store.audit_logs.list_entries_for_entity("shipment", str(shipment.shipment_id))) == 1


def test_accepted_with_exception_requires_exception_evidence() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(
        service, waybill_number="WB-3001"
    )

    with pytest.raises(ExceptionEvidenceRequired):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-3001",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
                exception_evidence=(),
            )
        )

    assert _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED


def test_accepted_with_exception_still_requires_prerequisites() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(
        service, waybill_number="WB-3002"
    )
    store.pickup_tasks.save_pickup_task(
        PickupTaskSnapshot(
            pickup_task_id=pickup_task.pickup_task_id,
            shipment_id=pickup_task.shipment_id,
            status=PickupTaskStatus.PENDING,
            assigned_driver_user_id=pickup_task.assigned_driver_user_id,
            assigned_batch_id=pickup_task.assigned_batch_id,
            has_pickup_condition_proof=True,
        )
    )

    with pytest.raises(PickupTaskNotProofCaptured):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-3002",
                scan_timestamp=_scan_time(),
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
                exception_evidence=(_evidence("s3://proof-bucket/exception-note-001.jpg"),),
            )
        )


def test_accepted_with_exception_starts_custody_and_sla_with_event() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(
        service, waybill_number="WB-3003"
    )
    scan_timestamp = _scan_time()

    service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier="WB-3003",
            scan_timestamp=scan_timestamp,
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            exception_evidence=(_evidence("s3://proof-bucket/exception-note-001.jpg"),),
        )
    )

    updated = store.shipments.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.current_status is ShipmentStatus.IN_CUSTODY
    assert updated.sla_started_at == scan_timestamp
    assert len(store.shipment_events.list_events_for_shipment(shipment.shipment_id)) == 1


def test_scanned_identifier_matches_shipment_id_string() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = _seed_ready_acceptance(service)

    service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier=str(shipment.shipment_id),
            scan_timestamp=_scan_time(),
            outcome=AcceptanceOutcome.ACCEPTED,
        )
    )

    assert _shipment_status(store, shipment.shipment_id) is ShipmentStatus.IN_CUSTODY


def test_inline_media_bytes_are_rejected() -> None:
    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference(b"inline-bytes")  # type: ignore[arg-type]

    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference("data:image/jpeg;base64,abcd")

    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference("A" * 300)
