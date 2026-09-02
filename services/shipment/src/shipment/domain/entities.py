"""Core Shipment domain entities — order intent through acceptance scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from shipment.domain.value_objects import (
    AcceptanceOutcome,
    ApproximateParcelMetrics,
    EvidenceReference,
    PackagingSealAssessment,
    WaybillIdentity,
)


@dataclass(slots=True)
class OrderIntent:
    """Order creation expresses intent only — no Hodhod custody or SLA (source §2, §3)."""

    order_id: UUID
    shipment_id: UUID
    created_at: datetime


@dataclass(slots=True)
class AcceptanceDecisionRecord:
    """Traceable internal record of an acceptance decision (source §5, §33)."""

    record_id: UUID
    waybill_identity: WaybillIdentity
    scan_timestamp: datetime
    responsible_operator_id: str
    packaging_seal_assessment: PackagingSealAssessment
    approximate_metrics: ApproximateParcelMetrics | None
    parcel_condition_evidence: tuple[EvidenceReference, ...]
    exception_evidence: tuple[EvidenceReference, ...]
    outcome: AcceptanceOutcome
    recorded_at: datetime


@dataclass(slots=True)
class Shipment:
    """Parcel aggregate bounded at acceptance scan — no post-acceptance lifecycle here."""

    shipment_id: UUID
    order_id: UUID
    waybill_identity: WaybillIdentity
    order_created_at: datetime
    custody_started_at: datetime | None = None
    sla_started_at: datetime | None = None
    in_hodhod_network: bool = False
    acceptance_record: AcceptanceDecisionRecord | None = field(default=None)

    @property
    def custody_active(self) -> bool:
        return self.custody_started_at is not None

    @property
    def sla_active(self) -> bool:
        return self.sla_started_at is not None
