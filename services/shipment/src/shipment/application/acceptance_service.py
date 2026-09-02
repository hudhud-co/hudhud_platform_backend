"""Acceptance lifecycle application service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from shipment.domain.entities import (
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ActingDriverNotAssigned,
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
    exception_evidence: tuple[EvidenceReference, ...] = ()
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AcceptanceScanResult:
    shipment: Shipment
    pickup_task: PickupTaskSnapshot
    shipment_event: ShipmentEvent | None
    audit_log: AuditLogEntry


class AcceptanceLifecycleService:
    """Order intent → acceptance scan boundary (Phase 11 / source §2, §3, §5)."""

    def __init__(self, unit_of_work: AcceptanceUnitOfWork) -> None:
        self._uow = unit_of_work

    def create_order_intent(
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
        self._uow.shipments.save_order_intent(order_intent)
        self._uow.shipments.save_shipment(shipment)
        return order_intent, shipment

    def register_pickup_task(self, command: RegisterPickupTaskCommand) -> PickupTaskSnapshot:
        """Persist service-local Pickup prerequisite input (production adapter deferred)."""
        pickup_task = PickupTaskSnapshot(
            pickup_task_id=command.pickup_task_id,
            shipment_id=command.shipment_id,
            status=PickupTaskStatus.PROOF_CAPTURED,
            assigned_driver_user_id=command.assigned_driver_user_id,
            assigned_batch_id=command.assigned_batch_id,
            has_pickup_condition_proof=command.has_pickup_condition_proof,
        )
        self._uow.pickup_tasks.save_pickup_task(pickup_task)
        return pickup_task

    def record_acceptance_scan(
        self,
        command: RecordAcceptanceScanCommand,
    ) -> AcceptanceScanResult:
        """Record acceptance scan and apply Phase 11 effects atomically."""
        recorded_at = command.recorded_at or command.scan_timestamp
        self._uow.begin()
        try:
            result = self._apply_acceptance_scan(command, recorded_at)
        except Exception:
            self._uow.rollback()
            raise
        else:
            self._uow.commit()
            return result

    def _apply_acceptance_scan(
        self,
        command: RecordAcceptanceScanCommand,
        recorded_at: datetime,
    ) -> AcceptanceScanResult:
        shipment = self._uow.shipments.get_shipment(command.shipment_id)
        if shipment is None:
            raise ShipmentNotFound(str(command.shipment_id))

        pickup_task = self._uow.pickup_tasks.get_pickup_task(command.pickup_task_id)
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
            shipment.current_custody_type = CustodyType.DRIVER
            shipment.current_custody_id = assigned_driver
            shipment_event = ShipmentEvent(
                event_id=uuid4(),
                shipment_id=shipment.shipment_id,
                event_type=ShipmentEventType.ACCEPTANCE_SCAN,
                previous_status=ShipmentStatus.CREATED,
                new_status=ShipmentStatus.IN_CUSTODY,
                occurred_at=command.scan_timestamp,
            )
            self._uow.shipment_events.append_event(shipment_event)

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
        self._uow.audit_logs.append_entry(audit_log)
        self._uow.pickup_tasks.save_pickup_task(pickup_task)
        self._uow.shipments.save_shipment(shipment)

        return AcceptanceScanResult(
            shipment=shipment,
            pickup_task=pickup_task,
            shipment_event=shipment_event,
            audit_log=audit_log,
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
