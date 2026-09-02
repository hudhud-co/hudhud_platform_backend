"""Acceptance lifecycle application service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from shipment.domain.entities import AcceptanceDecisionRecord, OrderIntent, Shipment
from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ExceptionEvidenceRequired,
    ShipmentNotFound,
)
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    ApproximateParcelMetrics,
    EvidenceReference,
    PackagingSealAssessment,
    WaybillIdentity,
)
from shipment.ports.repository import ShipmentRepository


@dataclass(frozen=True, slots=True)
class CreateOrderIntentCommand:
    order_id: UUID
    waybill_number: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordAcceptanceScanCommand:
    shipment_id: UUID
    waybill_number: str
    scan_timestamp: datetime
    responsible_operator_id: str
    packaging_seal_assessment: PackagingSealAssessment
    approximate_metrics: ApproximateParcelMetrics | None
    parcel_condition_evidence: tuple[EvidenceReference, ...]
    exception_evidence: tuple[EvidenceReference, ...]
    outcome: AcceptanceOutcome
    recorded_at: datetime


class AcceptanceLifecycleService:
    """Order intent → acceptance scan boundary (source §2, §3, §5)."""

    def __init__(self, repository: ShipmentRepository) -> None:
        self._repository = repository

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
            order_created_at=command.created_at,
        )
        self._repository.save_order_intent(order_intent)
        self._repository.save_shipment(shipment)
        return order_intent, shipment

    def record_acceptance_scan(
        self,
        command: RecordAcceptanceScanCommand,
    ) -> AcceptanceDecisionRecord:
        """Record acceptance scan and apply custody/SLA rules for the outcome."""
        shipment = self._repository.get_shipment(command.shipment_id)
        if shipment is None:
            raise ShipmentNotFound(str(command.shipment_id))
        if shipment.acceptance_record is not None:
            raise AcceptanceAlreadyRecorded(str(command.shipment_id))

        if (
            command.outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION
            and not command.exception_evidence
        ):
            raise ExceptionEvidenceRequired()

        waybill_identity = WaybillIdentity(
            waybill_number=command.waybill_number,
            shipment_id=str(command.shipment_id),
        )
        record = AcceptanceDecisionRecord(
            record_id=uuid4(),
            waybill_identity=waybill_identity,
            scan_timestamp=command.scan_timestamp,
            responsible_operator_id=command.responsible_operator_id,
            packaging_seal_assessment=command.packaging_seal_assessment,
            approximate_metrics=command.approximate_metrics,
            parcel_condition_evidence=command.parcel_condition_evidence,
            exception_evidence=command.exception_evidence,
            outcome=command.outcome,
            recorded_at=command.recorded_at,
        )

        if command.outcome in (
            AcceptanceOutcome.ACCEPTED,
            AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
        ):
            shipment.custody_started_at = command.scan_timestamp
            shipment.sla_started_at = command.scan_timestamp
            shipment.in_hodhod_network = True
        else:
            shipment.custody_started_at = None
            shipment.sla_started_at = None
            shipment.in_hodhod_network = False

        shipment.acceptance_record = record
        shipment.waybill_identity = waybill_identity
        self._repository.save_shipment(shipment)
        return record
