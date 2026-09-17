"""Apply the canonical PICKUP_DRIVER → ORIGIN_HUB custody transfer.

Shipment is the sole writer of canonical custody (ADR-0003). Pickup states that a hub
physically received the parcel; this service decides whether that statement is
applicable to the shipment it holds, and records the transfer.

Delivery is at-least-once, so a redelivered fact must converge rather than conflict: a
repeat of the *same* transfer is accepted as already applied, while a *different* fact
claiming the same custody move is a permanent rejection for Operations to resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from shipment.domain.entities import (
    AuditLogEntry,
    CustodyTransferRecord,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import PoisonHandlerError
from shipment.domain.types import ValidatedPickupHandoverFact
from shipment.domain.value_objects import (
    CustodyType,
    HandoverOutcome,
    ShipmentEventType,
    ShipmentStatus,
)
from shipment.ports.accepted_fact import HandoverFactUnitOfWork


@dataclass(frozen=True, slots=True)
class CustodyTransferApplyResult:
    shipment: Shipment
    transfer: CustodyTransferRecord
    audit_log: AuditLogEntry
    shipment_event: ShipmentEvent | None
    previous_version: int
    new_version: int
    already_applied: bool = False


class PickupHandoverCustodyApplyService:
    """Move canonical custody to the receiving origin hub from a validated fact."""

    def __init__(self, unit_of_work: HandoverFactUnitOfWork) -> None:
        self._uow = unit_of_work

    def apply(
        self,
        fact: ValidatedPickupHandoverFact,
        *,
        recorded_at: datetime,
    ) -> CustodyTransferApplyResult:
        shipment = self._uow.shipments.get_shipment(fact.shipment_id)
        if shipment is None:
            raise PoisonHandlerError(
                "SHIPMENT_NOT_FOUND",
                f"shipment not found: {fact.shipment_id}",
            )

        existing = self._uow.custody_transfers.get_for_pickup_task(fact.pickup_task_id)
        if existing is not None:
            return self._converge_on_existing(shipment, existing, fact, recorded_at)

        self._assert_transferable(shipment, fact)

        previous_version = shipment.version
        new_version = previous_version + 1
        shipment.current_custody_type = CustodyType.ORIGIN_HUB
        shipment.current_custody_id = str(fact.receiving_hub_id)
        shipment.custody_transferred_at = fact.released_at
        shipment.version = new_version

        transfer = CustodyTransferRecord(
            transfer_id=uuid4(),
            shipment_id=shipment.shipment_id,
            pickup_task_id=fact.pickup_task_id,
            handover_manifest_id=fact.handover_manifest_id,
            from_custody_type=CustodyType.PICKUP_DRIVER,
            from_custody_id=fact.releasing_driver_user_id,
            to_custody_type=CustodyType.ORIGIN_HUB,
            to_custody_id=str(fact.receiving_hub_id),
            outcome=fact.outcome,
            discrepancy_reason=fact.discrepancy_reason,
            released_at=fact.released_at,
            recorded_at=recorded_at,
            receiving_actor_id=fact.receiving_actor_id,
            condition_evidence=fact.condition_evidence,
        )
        self._uow.custody_transfers.save(transfer)

        shipment_event = ShipmentEvent(
            event_id=uuid4(),
            shipment_id=shipment.shipment_id,
            event_type=ShipmentEventType.HUB_HANDOVER_RECEIPT,
            previous_status=ShipmentStatus.IN_CUSTODY,
            new_status=ShipmentStatus.IN_CUSTODY,
            occurred_at=fact.released_at,
        )
        self._uow.shipment_events.append_event(shipment_event)

        audit_details: dict[str, str] = {
            "outcome": fact.outcome.value,
            "pickup_task_id": str(fact.pickup_task_id),
            "handover_manifest_id": str(fact.handover_manifest_id),
            "releasing_driver_user_id": fact.releasing_driver_user_id,
            "receiving_hub_id": str(fact.receiving_hub_id),
            "released_at": fact.released_at.isoformat(),
            "source_event_id": str(fact.event_id),
            "pickup_aggregate_version": str(fact.aggregate_version),
            "shipment_version": str(new_version),
        }
        if fact.discrepancy_reason:
            audit_details["discrepancy_reason"] = fact.discrepancy_reason
        if fact.condition_evidence:
            audit_details["condition_evidence_uris"] = ",".join(
                evidence.storage_uri for evidence in fact.condition_evidence
            )
        audit_log = AuditLogEntry(
            audit_id=uuid4(),
            action="SHIPMENT_HUB_HANDOVER_RECEIPT",
            entity_type="shipment",
            entity_id=str(shipment.shipment_id),
            actor_id=fact.receiving_actor_id,
            occurred_at=recorded_at,
            details=audit_details,
        )
        self._uow.audit_logs.append_entry(audit_log)
        self._uow.shipments.save_shipment(shipment)

        return CustodyTransferApplyResult(
            shipment=shipment,
            transfer=transfer,
            audit_log=audit_log,
            shipment_event=shipment_event,
            previous_version=previous_version,
            new_version=new_version,
        )

    def _assert_transferable(
        self, shipment: Shipment, fact: ValidatedPickupHandoverFact
    ) -> None:
        if shipment.current_status is not ShipmentStatus.IN_CUSTODY:
            raise PoisonHandlerError(
                "CUSTODY_NOT_STARTED",
                (
                    f"shipment {shipment.shipment_id} is "
                    f"{shipment.current_status.value}; custody has not started"
                ),
            )
        if shipment.current_custody_type is not CustodyType.PICKUP_DRIVER:
            raise PoisonHandlerError(
                "CUSTODY_CONFLICT",
                (
                    "shipment is not in pickup-driver custody "
                    f"(current={shipment.current_custody_type})"
                ),
            )
        if shipment.current_custody_id != fact.releasing_driver_user_id:
            # The fact claims a release by someone who does not hold the parcel.
            raise PoisonHandlerError(
                "CUSTODY_HOLDER_MISMATCH",
                "releasing driver does not hold canonical custody of this shipment",
            )

    def _converge_on_existing(
        self,
        shipment: Shipment,
        existing: CustodyTransferRecord,
        fact: ValidatedPickupHandoverFact,
        recorded_at: datetime,
    ) -> CustodyTransferApplyResult:
        """A redelivery of the same transfer converges; a different one is poison."""
        same_transfer = (
            existing.shipment_id == fact.shipment_id
            and existing.handover_manifest_id == fact.handover_manifest_id
            and existing.to_custody_id == str(fact.receiving_hub_id)
            and existing.outcome is fact.outcome
        )
        if not same_transfer:
            raise PoisonHandlerError(
                "CUSTODY_CONFLICT",
                (
                    "a different custody transfer is already recorded for pickup task "
                    f"{fact.pickup_task_id}"
                ),
            )
        audit_log = AuditLogEntry(
            audit_id=uuid4(),
            action="SHIPMENT_HUB_HANDOVER_RECEIPT_DUPLICATE",
            entity_type="shipment",
            entity_id=str(shipment.shipment_id),
            actor_id=fact.receiving_actor_id,
            occurred_at=recorded_at,
            details={
                "pickup_task_id": str(fact.pickup_task_id),
                "source_event_id": str(fact.event_id),
                "existing_transfer_id": str(existing.transfer_id),
            },
        )
        self._uow.audit_logs.append_entry(audit_log)
        return CustodyTransferApplyResult(
            shipment=shipment,
            transfer=existing,
            audit_log=audit_log,
            shipment_event=None,
            previous_version=shipment.version,
            new_version=shipment.version,
            already_applied=True,
        )


def custody_release_is_irreversible(outcome: HandoverOutcome) -> bool:
    """Both releasing outcomes are physical facts; a dispute does not undo them."""
    return outcome in (
        HandoverOutcome.RECEIVED,
        HandoverOutcome.RECEIVED_WITH_DISCREPANCY,
    )
