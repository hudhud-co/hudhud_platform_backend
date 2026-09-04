"""Acceptance lifecycle application service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from shipment.domain.entities import (
    AcceptanceIdempotencyRecord,
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ActingDriverNotAssigned,
    ConflictingIdempotencyKey,
    ExceptionEvidenceRequired,
    PickupConditionProofMissing,
    PickupTaskMissingAssignedBatch,
    PickupTaskMissingAssignedDriver,
    PickupTaskNotFound,
    PickupTaskNotProofCaptured,
    ScannedIdentifierMismatch,
    ShipmentNotCreated,
    ShipmentNotFound,
)
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    EvidenceReference,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.ports.repository import AcceptanceUnitOfWork


@dataclass(frozen=True, slots=True)
class CreateOrderIntentCommand:
    order_id: UUID
    waybill_number: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RegisterPickupTaskCommand:
    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    assigned_batch_id: UUID
    has_pickup_condition_proof: bool


@dataclass(frozen=True, slots=True)
class RecordAcceptanceScanCommand:
    shipment_id: UUID
    pickup_task_id: UUID
    acting_driver_user_id: str
    scanned_identifier: str
    scan_timestamp: datetime
    outcome: AcceptanceOutcome
    idempotency_key: str
    exception_evidence: tuple[EvidenceReference, ...] = ()
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AcceptanceScanResult:
    shipment: Shipment
    pickup_task: PickupTaskSnapshot
    shipment_event: ShipmentEvent | None
    audit_log: AuditLogEntry
    idempotent_replay: bool = False


class AcceptanceLifecycleService:
    """Order intent → acceptance scan boundary (Phase 11 / source §2, §3, §5)."""

    def __init__(self, unit_of_work: AcceptanceUnitOfWork) -> None:
        self._uow = unit_of_work

    async def create_order_intent(
        self,
        command: CreateOrderIntentCommand,
    ) -> tuple[OrderIntent, Shipment]:
        """Create order intent without starting Hodhod custody or SLA."""
        shipment_id = uuid4()
        waybill_identity = WaybillIdentity(
            waybill_number=command.waybill_number,
            shipment_id=str(shipment_id),
        )
        order_intent = OrderIntent(
            order_id=command.order_id,
            shipment_id=shipment_id,
            created_at=command.created_at,
        )
        shipment = Shipment(
            shipment_id=shipment_id,
            order_id=command.order_id,
            waybill_identity=waybill_identity,
            current_status=ShipmentStatus.CREATED,
            order_created_at=command.created_at,
        )
        await self._uow.shipments.save_order_intent(order_intent)
        await self._uow.shipments.save_shipment(shipment)
        return order_intent, shipment

    async def register_pickup_task(self, command: RegisterPickupTaskCommand) -> PickupTaskSnapshot:
        """Persist service-local Pickup prerequisite input (production adapter deferred)."""
        pickup_task = PickupTaskSnapshot(
            pickup_task_id=command.pickup_task_id,
            shipment_id=command.shipment_id,
            status=PickupTaskStatus.PROOF_CAPTURED,
            assigned_driver_user_id=command.assigned_driver_user_id,
            assigned_batch_id=command.assigned_batch_id,
            has_pickup_condition_proof=command.has_pickup_condition_proof,
        )
        await self._uow.pickup_tasks.save_pickup_task(pickup_task)
        return pickup_task

    async def record_acceptance_scan(
        self,
        command: RecordAcceptanceScanCommand,
    ) -> AcceptanceScanResult:
        """Record acceptance scan and apply Phase 11 effects atomically."""
        if not command.idempotency_key.strip():
            msg = "idempotency key is required"
            raise ValueError(msg)

        fingerprint = _command_fingerprint(command)
        cached = await self._uow.idempotency.get_record(command.idempotency_key)
        if cached is not None:
            if cached.command_fingerprint != fingerprint:
                raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
            return await self._reconstruct_cached_result(cached)

        recorded_at = command.recorded_at or command.scan_timestamp
        await self._uow.begin()
        try:
            cached = await self._uow.idempotency.get_record(command.idempotency_key)
            if cached is not None:
                if cached.command_fingerprint != fingerprint:
                    raise ConflictingIdempotencyKey(idempotency_key=command.idempotency_key)
                result = await self._reconstruct_cached_result(cached)
            else:
                result = await self._apply_acceptance_scan(command, recorded_at, fingerprint)
        except Exception:
            await self._uow.rollback()
            raise
        else:
            await self._uow.commit()
            return result

    async def _apply_acceptance_scan(
        self,
        command: RecordAcceptanceScanCommand,
        recorded_at: datetime,
        fingerprint: str,
    ) -> AcceptanceScanResult:
        shipment = await self._uow.shipments.get_shipment(command.shipment_id)
        if shipment is None:
            raise ShipmentNotFound(str(command.shipment_id))

        pickup_task = await self._uow.pickup_tasks.get_pickup_task(command.pickup_task_id)
        if pickup_task is None:
            raise PickupTaskNotFound(str(command.pickup_task_id))

        if pickup_task.acceptance_state is not None:
            raise AcceptanceAlreadyRecorded(str(command.shipment_id))

        self._validate_prerequisites(
            shipment=shipment,
            pickup_task=pickup_task,
            acting_driver_user_id=command.acting_driver_user_id,
            scanned_identifier=command.scanned_identifier,
        )

        if (
            command.outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION
            and not command.exception_evidence
        ):
            raise ExceptionEvidenceRequired()

        pickup_acceptance_state = _pickup_acceptance_state(command.outcome)
        pickup_task.acceptance_state = pickup_acceptance_state

        shipment_event: ShipmentEvent | None = None
        if command.outcome in (
            AcceptanceOutcome.ACCEPTED,
            AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
        ):
            assigned_driver = pickup_task.assigned_driver_user_id
            assert assigned_driver is not None  # validated in prerequisites
            shipment.current_status = ShipmentStatus.IN_CUSTODY
            shipment.accepted_at = command.scan_timestamp
            shipment.sla_started_at = command.scan_timestamp
            shipment.current_custody_type = CustodyType.PICKUP_DRIVER
            shipment.current_custody_id = assigned_driver
            shipment_event = ShipmentEvent(
                event_id=uuid4(),
                shipment_id=shipment.shipment_id,
                event_type=ShipmentEventType.ACCEPTANCE_SCAN,
                previous_status=ShipmentStatus.CREATED,
                new_status=ShipmentStatus.IN_CUSTODY,
                occurred_at=command.scan_timestamp,
            )
            await self._uow.shipment_events.append_event(shipment_event)

        audit_details: dict[str, str] = {
            "outcome": command.outcome.value,
            "pickup_task_id": str(pickup_task.pickup_task_id),
            "scanned_identifier": command.scanned_identifier.strip(),
            "scan_timestamp": command.scan_timestamp.isoformat(),
        }
        if command.exception_evidence:
            audit_details["exception_evidence_uris"] = ",".join(
                evidence.storage_uri for evidence in command.exception_evidence
            )
        audit_log = AuditLogEntry(
            audit_id=uuid4(),
            action="SHIPMENT_ACCEPTANCE_SCAN",
            entity_type="shipment",
            entity_id=str(shipment.shipment_id),
            actor_id=command.acting_driver_user_id,
            occurred_at=recorded_at,
            details=audit_details,
        )
        await self._uow.audit_logs.append_entry(audit_log)
        await self._uow.pickup_tasks.save_pickup_task(pickup_task)
        await self._uow.shipments.save_shipment(shipment)
        await self._uow.idempotency.save_record(
            AcceptanceIdempotencyRecord(
                idempotency_key=command.idempotency_key,
                command_fingerprint=fingerprint,
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task.pickup_task_id,
                recorded_at=recorded_at,
            )
        )

        return AcceptanceScanResult(
            shipment=shipment,
            pickup_task=pickup_task,
            shipment_event=shipment_event,
            audit_log=audit_log,
        )

    async def _reconstruct_cached_result(
        self,
        cached: AcceptanceIdempotencyRecord,
    ) -> AcceptanceScanResult:
        shipment = await self._uow.shipments.get_shipment(cached.shipment_id)
        if shipment is None:
            raise ShipmentNotFound(str(cached.shipment_id))
        pickup_task = await self._uow.pickup_tasks.get_pickup_task(cached.pickup_task_id)
        if pickup_task is None:
            raise PickupTaskNotFound(str(cached.pickup_task_id))
        events = await self._uow.shipment_events.list_events_for_shipment(cached.shipment_id)
        shipment_event = events[-1] if events else None
        audit_entries = await self._uow.audit_logs.list_entries_for_entity(
            "shipment", str(cached.shipment_id)
        )
        if not audit_entries:
            msg = "idempotent replay missing audit log"
            raise RuntimeError(msg)
        return AcceptanceScanResult(
            shipment=shipment,
            pickup_task=pickup_task,
            shipment_event=shipment_event,
            audit_log=audit_entries[-1],
            idempotent_replay=True,
        )

    def _validate_prerequisites(
        self,
        *,
        shipment: Shipment,
        pickup_task: PickupTaskSnapshot,
        acting_driver_user_id: str,
        scanned_identifier: str,
    ) -> None:
        if pickup_task.status is not PickupTaskStatus.PROOF_CAPTURED:
            raise PickupTaskNotProofCaptured(
                pickup_task_id=str(pickup_task.pickup_task_id),
                current_status=pickup_task.status.value,
            )
        if pickup_task.assigned_driver_user_id is None:
            raise PickupTaskMissingAssignedDriver(pickup_task_id=str(pickup_task.pickup_task_id))
        if pickup_task.assigned_batch_id is None:
            raise PickupTaskMissingAssignedBatch(pickup_task_id=str(pickup_task.pickup_task_id))
        if acting_driver_user_id != pickup_task.assigned_driver_user_id:
            raise ActingDriverNotAssigned(
                pickup_task_id=str(pickup_task.pickup_task_id),
                acting_driver_user_id=acting_driver_user_id,
            )
        if shipment.current_status is not ShipmentStatus.CREATED:
            raise ShipmentNotCreated(
                shipment_id=str(shipment.shipment_id),
                current_status=shipment.current_status.value,
            )
        if not pickup_task.has_pickup_condition_proof:
            raise PickupConditionProofMissing(pickup_task_id=str(pickup_task.pickup_task_id))
        if not _scanned_identifier_matches(shipment, scanned_identifier):
            raise ScannedIdentifierMismatch(
                shipment_id=str(shipment.shipment_id),
                scanned_identifier=scanned_identifier,
            )


def _scanned_identifier_matches(shipment: Shipment, scanned_identifier: str) -> bool:
    normalized = scanned_identifier.strip()
    identity = shipment.waybill_identity
    return normalized in (identity.waybill_number, identity.shipment_id)


def _pickup_acceptance_state(outcome: AcceptanceOutcome) -> PickupTaskAcceptanceState:
    if outcome is AcceptanceOutcome.ACCEPTED:
        return PickupTaskAcceptanceState.ACCEPTED
    if outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION:
        return PickupTaskAcceptanceState.ACCEPTED_WITH_EXCEPTION
    return PickupTaskAcceptanceState.REJECTED


def _command_fingerprint(command: RecordAcceptanceScanCommand) -> str:
    payload = {
        "shipment_id": str(command.shipment_id),
        "pickup_task_id": str(command.pickup_task_id),
        "scanned_identifier": command.scanned_identifier.strip(),
        "outcome": command.outcome.value,
        "exception_evidence_uris": [
            evidence.storage_uri for evidence in command.exception_evidence
        ],
        "scan_timestamp": command.scan_timestamp.isoformat(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
