"""HTTP request/response schemas for acceptance scan commands."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shipment.domain.value_objects import AcceptanceOutcome


class EvidenceReferenceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    storage_uri: str
    captured_at: datetime | None = None
    location_label: str | None = None


class AcceptanceScanRequest(BaseModel):
    """Acceptance scan command body — actor identity is never accepted here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pickup_task_id: UUID
    scanned_identifier: str = Field(min_length=1)
    scan_timestamp: datetime
    outcome: AcceptanceOutcome
    exception_evidence: tuple[EvidenceReferenceRequest, ...] = ()


class AcceptanceScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    shipment_id: UUID
    pickup_task_id: UUID
    current_status: str
    acceptance_state: str | None
    outcome: str
    accepted_at: datetime | None
    sla_started_at: datetime | None
    current_custody_type: str | None
    current_custody_id: str | None
    shipment_event_id: UUID | None
    audit_id: UUID
    idempotent_replay: bool = False
