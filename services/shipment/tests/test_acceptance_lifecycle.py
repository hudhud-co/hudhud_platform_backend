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
    ConflictingIdempotencyKey,
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


async def _shipment_status(
    store: InMemoryAcceptanceUnitOfWork, shipment_id: UUID
) -> ShipmentStatus:
    shipment = await store.shipments.get_shipment(shipment_id)
    assert shipment is not None
    return shipment.current_status


async def _seed_ready_acceptance(
    service: AcceptanceLifecycleService,
    *,
    waybill_number: str = "WB-1001",
    driver_id: str = "driver-42",
) -> tuple[object, object, PickupTaskSnapshot]:
    _order, shipment = await service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number=waybill_number,
            created_at=_created_at(),
        )
    )
    pickup_task_id = uuid4()
    batch_id = uuid4()
    pickup_task = await service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=batch_id,
            has_pickup_condition_proof=True,
        )
    )
    return shipment, pickup_task_id, pickup_task


def _scan_command(
    shipment_id: UUID,
    pickup_task_id: UUID,
    *,
    driver_id: str = "driver-42",
    scanned_identifier: str = "WB-1001",
    outcome: AcceptanceOutcome = AcceptanceOutcome.ACCEPTED,
    idempotency_key: str | None = None,
    exception_evidence: tuple[EvidenceReference, ...] = (),
    scan_timestamp: datetime | None = None,
) -> RecordAcceptanceScanCommand:
    return RecordAcceptanceScanCommand(
        shipment_id=shipment_id,
        pickup_task_id=pickup_task_id,
        acting_driver_user_id=driver_id,
        scanned_identifier=scanned_identifier,
        scan_timestamp=scan_timestamp or _scan_time(),
        outcome=outcome,
        idempotency_key=idempotency_key or f"idem-{uuid4()}",
        exception_evidence=exception_evidence,
        recorded_at=scan_timestamp or _scan_time(),
    )


async def test_new_order_has_no_custody_or_sla_start() -> None:
    service, _store = _service()
    _order, shipment = await service.create_order_intent(
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


async def test_successful_acceptance_produces_all_atomic_effects() -> None:
    service, store = _service()
    shipment, pickup_task_id, _pickup = await _seed_ready_acceptance(service)
    scan_timestamp = _scan_time()
    driver_id = "driver-42"

    result = await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=driver_id,
            scan_timestamp=scan_timestamp,
        )
    )

    updated = await store.shipments.get_shipment(shipment.shipment_id)
    updated_pickup = await store.pickup_tasks.get_pickup_task(pickup_task_id)
    events = await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    audit_entries = await store.audit_logs.list_entries_for_entity(
        "shipment", str(shipment.shipment_id)
    )

    assert updated is not None
    assert updated.current_status is ShipmentStatus.IN_CUSTODY
    assert updated.accepted_at == scan_timestamp
    assert updated.sla_started_at == scan_timestamp
    assert updated.current_custody_type is CustodyType.PICKUP_DRIVER
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


async def test_custody_and_sla_start_at_same_scan_timestamp() -> None:
    service, store = _service()
    shipment, pickup_task_id, _pickup = await _seed_ready_acceptance(
        service, waybill_number="WB-2001"
    )
    scan_timestamp = _scan_time()

    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            scanned_identifier="WB-2001",
            scan_timestamp=scan_timestamp,
        )
    )

    updated = await store.shipments.get_shipment(shipment.shipment_id)
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
async def test_prerequisite_violations_raise_explicit_errors(
    mutator,
    expected_error,
) -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    await store.pickup_tasks.save_pickup_task(mutator(pickup_task))

    acting_driver = (
        "wrong-driver"
        if expected_error is ActingDriverNotAssigned
        else pickup_task.assigned_driver_user_id or "driver-42"
    )

    with pytest.raises(expected_error):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=acting_driver,
            )
        )

    assert await _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED
    assert not await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert not await store.audit_logs.list_entries_for_entity(
        "shipment", str(shipment.shipment_id)
    )


async def test_shipment_not_created_rejects_acceptance() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    shipment.current_status = ShipmentStatus.IN_CUSTODY
    await store.shipments.save_shipment(shipment)

    with pytest.raises(ShipmentNotCreated):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=pickup_task.assigned_driver_user_id or "driver-42",
            )
        )


async def test_mismatched_scanned_identifier_rejects_acceptance() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)

    with pytest.raises(ScannedIdentifierMismatch):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-9999",
            )
        )

    assert not await store.shipment_events.list_events_for_shipment(shipment.shipment_id)


async def test_repeated_acceptance_cannot_overwrite() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    scan_timestamp = _scan_time()
    driver = pickup_task.assigned_driver_user_id or "driver-42"
    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=driver,
            scan_timestamp=scan_timestamp,
            idempotency_key="key-1",
        )
    )

    with pytest.raises(AcceptanceAlreadyRecorded):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=driver,
                scan_timestamp=scan_timestamp,
                outcome=AcceptanceOutcome.REJECTED,
                idempotency_key="key-2",
            )
        )

    events = await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert len(events) == 1
    assert events[0].event_type is ShipmentEventType.ACCEPTANCE_SCAN


async def test_idempotent_replay_returns_same_result() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    command = _scan_command(
        shipment.shipment_id,
        pickup_task_id,
        driver_id=pickup_task.assigned_driver_user_id or "driver-42",
        idempotency_key="replay-key",
    )
    first = await service.record_acceptance_scan(command)
    second = await service.record_acceptance_scan(command)
    assert second.idempotent_replay is True
    assert second.shipment.shipment_id == first.shipment.shipment_id
    events = await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert len(events) == 1


async def test_conflicting_idempotency_key_rejected() -> None:
    service, _store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    driver = pickup_task.assigned_driver_user_id or "driver-42"
    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=driver,
            idempotency_key="same-key",
        )
    )
    with pytest.raises(ConflictingIdempotencyKey):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=driver,
                scanned_identifier=str(shipment.shipment_id),
                idempotency_key="same-key",
            )
        )


async def test_rollback_leaves_all_stores_unchanged_on_commit_failure() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)
    store.fail_on_commit = True

    with pytest.raises(SimulatedCommitFailure):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=pickup_task.assigned_driver_user_id or "driver-42",
            )
        )

    assert await _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED
    pickup = await store.pickup_tasks.get_pickup_task(pickup_task_id)
    assert pickup is not None
    assert pickup.acceptance_state is None
    assert not await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert not await store.audit_logs.list_entries_for_entity(
        "shipment", str(shipment.shipment_id)
    )
    assert "rollback" in store.actions


async def test_rejected_outcome_does_not_start_custody_sla_or_acceptance_scan_event() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(
        service, waybill_number="WB-4001"
    )

    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier="WB-4001",
            outcome=AcceptanceOutcome.REJECTED,
        )
    )

    updated = await store.shipments.get_shipment(shipment.shipment_id)
    updated_pickup = await store.pickup_tasks.get_pickup_task(pickup_task_id)
    assert updated is not None
    assert updated.current_status is ShipmentStatus.CREATED
    assert updated.accepted_at is None
    assert updated.sla_started_at is None
    assert updated.current_custody_type is None
    assert updated_pickup is not None
    assert updated_pickup.acceptance_state is PickupTaskAcceptanceState.REJECTED
    assert not await store.shipment_events.list_events_for_shipment(shipment.shipment_id)
    assert len(
        await store.audit_logs.list_entries_for_entity("shipment", str(shipment.shipment_id))
    ) == 1


async def test_accepted_with_exception_requires_exception_evidence() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(
        service, waybill_number="WB-3001"
    )

    with pytest.raises(ExceptionEvidenceRequired):
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-3001",
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            )
        )

    assert await _shipment_status(store, shipment.shipment_id) is ShipmentStatus.CREATED


async def test_accepted_with_exception_still_requires_prerequisites() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(
        service, waybill_number="WB-3002"
    )
    await store.pickup_tasks.save_pickup_task(
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
        await service.record_acceptance_scan(
            _scan_command(
                shipment.shipment_id,
                pickup_task_id,
                driver_id=pickup_task.assigned_driver_user_id or "driver-42",
                scanned_identifier="WB-3002",
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
                exception_evidence=(_evidence("s3://proof-bucket/exception-note-001.jpg"),),
            )
        )


async def test_accepted_with_exception_starts_custody_and_sla_with_event() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(
        service, waybill_number="WB-3003"
    )
    scan_timestamp = _scan_time()

    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier="WB-3003",
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            exception_evidence=(_evidence("s3://proof-bucket/exception-note-001.jpg"),),
            scan_timestamp=scan_timestamp,
        )
    )

    updated = await store.shipments.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.current_status is ShipmentStatus.IN_CUSTODY
    assert updated.sla_started_at == scan_timestamp
    assert updated.current_custody_type is CustodyType.PICKUP_DRIVER
    assert len(await store.shipment_events.list_events_for_shipment(shipment.shipment_id)) == 1


async def test_scanned_identifier_matches_shipment_id_string() -> None:
    service, store = _service()
    shipment, pickup_task_id, pickup_task = await _seed_ready_acceptance(service)

    await service.record_acceptance_scan(
        _scan_command(
            shipment.shipment_id,
            pickup_task_id,
            driver_id=pickup_task.assigned_driver_user_id or "driver-42",
            scanned_identifier=str(shipment.shipment_id),
        )
    )

    assert await _shipment_status(store, shipment.shipment_id) is ShipmentStatus.IN_CUSTODY


def test_inline_media_bytes_are_rejected() -> None:
    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference(b"inline-bytes")  # type: ignore[arg-type]

    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference("data:image/jpeg;base64,abcd")

    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference("A" * 300)
