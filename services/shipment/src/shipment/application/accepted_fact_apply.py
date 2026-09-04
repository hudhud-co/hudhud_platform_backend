"""Native pickup.fact.accepted application — no PickupTaskSnapshot dependency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AuditLogEntry,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import (
    ExceptionEvidenceRequired,
    PoisonHandlerError,
    PreExistingAcceptanceConflict,
    ScannedIdentifierMismatch,
    ShipmentNotCreated,
    ShipmentNotFound,
)
from shipment.domain.types import ValidatedPickupAcceptedFact
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    ShipmentEventType,
    ShipmentStatus,
)
from shipment.ports.accepted_fact import AcceptedFactUnitOfWork


@dataclass(frozen=True, slots=True)
class NativeAcceptanceApplyResult:
    shipment: Shipment
    shipment_event: ShipmentEvent
    audit_log: AuditLogEntry
    decision: AcceptanceDecisionRecord
    previous_version: int
    new_version: int


class NativePickupAcceptedApplyService:
    """Apply custody-starting acceptance from a validated Pickup fact only."""

    def __init__(self, unit_of_work: AcceptedFactUnitOfWork) -> None:
        self._uow = unit_of_work

    def apply(
        self,
        fact: ValidatedPickupAcceptedFact,
        *,
        recorded_at: datetime,
    ) -> NativeAcceptanceApplyResult:
        shipment = self._uow.shipments.get_shipment(fact.shipment_id)
        if shipment is None:
            raise PoisonHandlerError(
                "SHIPMENT_NOT_FOUND",
                f"shipment not found: {fact.shipment_id}",
            )

        existing_decision = self._uow.acceptance_decisions.get_for_shipment(fact.shipment_id)
        if existing_decision is not None:
            raise PoisonHandlerError(
                "ACCEPTANCE_CONFLICT",
                (
                    "pre-existing acceptance decision conflicts with native fact apply; "
                    "not treated as this fact's processed delivery"
                ),
            )

        _assert_shipment_ready_for_native_acceptance(shipment)

        if not _scanned_identifier_matches(shipment, fact.scanned_identifier):
            raise PoisonHandlerError(
                "SCANNED_IDENTIFIER_MISMATCH",
                (
                    f"scanned identifier {fact.scanned_identifier!r} does not match "
                    f"shipment {fact.shipment_id}"
                ),
            )

        if (
            fact.outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION
            and not fact.exception_evidence
        ):
            raise PoisonHandlerError(
                "EXCEPTION_EVIDENCE_REQUIRED",
                "accepted-with-exception requires exception evidence references",
            )

        previous_version = shipment.version
        new_version = previous_version + 1

        shipment.current_status = ShipmentStatus.IN_CUSTODY
        shipment.current_custody_type = CustodyType.PICKUP_DRIVER
        shipment.current_custody_id = fact.assigned_driver_user_id
        shipment.accepted_at = fact.accepted_at
        shipment.sla_started_at = fact.accepted_at
        shipment.version = new_version

        shipment_event = ShipmentEvent(
            event_id=uuid4(),
            shipment_id=shipment.shipment_id,
            event_type=ShipmentEventType.ACCEPTANCE_SCAN,
            previous_status=ShipmentStatus.CREATED,
            new_status=ShipmentStatus.IN_CUSTODY,
            occurred_at=fact.accepted_at,
        )
        self._uow.shipment_events.append_event(shipment_event)

        audit_details: dict[str, str] = {
            "outcome": fact.outcome.value,
            "pickup_task_id": str(fact.pickup_task_id),
            "scanned_identifier": fact.scanned_identifier,
            "scan_timestamp": fact.accepted_at.isoformat(),
            "source_event_id": str(fact.event_id),
            "pickup_aggregate_version": str(fact.aggregate_version),
            "shipment_version": str(new_version),
        }
        if fact.exception_evidence:
            audit_details["exception_evidence_uris"] = ",".join(
                evidence.storage_uri for evidence in fact.exception_evidence
            )
        audit_log = AuditLogEntry(
            audit_id=uuid4(),
            action="SHIPMENT_ACCEPTANCE_SCAN",
            entity_type="shipment",
            entity_id=str(shipment.shipment_id),
            actor_id=fact.acting_driver_user_id,
            occurred_at=recorded_at,
            details=audit_details,
        )
        self._uow.audit_logs.append_entry(audit_log)

        decision = AcceptanceDecisionRecord(
            decision_id=uuid4(),
            shipment_id=shipment.shipment_id,
            pickup_task_id=fact.pickup_task_id,
            outcome=fact.outcome,
            acting_driver_user_id=fact.acting_driver_user_id,
            scanned_identifier=fact.scanned_identifier,
            scan_timestamp=fact.accepted_at,
            recorded_at=recorded_at,
            exception_evidence=fact.exception_evidence,
        )
        self._uow.acceptance_decisions.save(decision)
        self._uow.shipments.save_shipment(shipment)

        return NativeAcceptanceApplyResult(
            shipment=shipment,
            shipment_event=shipment_event,
            audit_log=audit_log,
            decision=decision,
            previous_version=previous_version,
            new_version=new_version,
        )


def _assert_shipment_ready_for_native_acceptance(shipment: Shipment) -> None:
    if (
        shipment.accepted_at is not None
        or shipment.sla_started_at is not None
        or shipment.current_custody_type is not None
        or shipment.current_custody_id is not None
    ):
        raise PoisonHandlerError(
            "ACCEPTANCE_CONFLICT",
            "shipment already has custody or acceptance timestamps",
        )
    if shipment.current_status is not ShipmentStatus.CREATED:
        if shipment.current_status is ShipmentStatus.IN_CUSTODY:
            raise PoisonHandlerError(
                "ACCEPTANCE_CONFLICT",
                "shipment already IN_CUSTODY from a prior acceptance path",
            )
        raise PoisonHandlerError(
            "SHIPMENT_NOT_CREATED",
            (
                f"shipment {shipment.shipment_id} not CREATED "
                f"(current={shipment.current_status.value})"
            ),
        )


def _scanned_identifier_matches(shipment: Shipment, scanned_identifier: str) -> bool:
    normalized = scanned_identifier.strip()
    identity = shipment.waybill_identity
    return normalized in (identity.waybill_number, identity.shipment_id)


# Re-export domain errors used by HTTP path tests that may import helpers.
__all__ = [
    "ExceptionEvidenceRequired",
    "NativeAcceptanceApplyResult",
    "NativePickupAcceptedApplyService",
    "PreExistingAcceptanceConflict",
    "ScannedIdentifierMismatch",
    "ShipmentNotCreated",
    "ShipmentNotFound",
]
