"""Request and response bodies for the Claims HTTP adapter.

The driver-facing models here have **no amount field**, exactly as the entities they are
built from have none (SEC-07). That is worth stating twice, because a response model is
the last place a value could leak after the domain has been careful.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from claims.domain.value_objects import (
    ClaimKind,
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    CustodyBoundary,
    IncidentKind,
    RejectionReason,
)


class EvidenceRef(BaseModel):
    """A pointer to stored evidence. Claims never carries the bytes."""

    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=128)


class MoneyResponse(BaseModel):
    """Integer minor units and an explicit currency. Never a float (ADR-0012)."""

    minor_units: int
    currency: str


# ------------------------------------------------------------------ claimant


class FileClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tracking_code: str = Field(min_length=1, max_length=32)
    kind: ClaimKind
    description: str | None = Field(default=None, max_length=4000)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    #: v6.3 p.37 — where HUDHUD's responsibility for this parcel stopped. Defaults to
    #: the ordinary case; a claim about a parcel taken inside to test is refused.
    custody_boundary: CustodyBoundary = CustodyBoundary.IN_HUDHUD_CUSTODY
    #: CLM-01 — who is compensated. Omitted means the caller is the sender.
    sender_principal_id: UUID | None = None
    merchant_id: UUID | None = None


class FiledClaimResponse(BaseModel):
    """What `claimSubmitted` shows: a reference and nothing else to act on."""

    reference: str
    tracking_code: str
    kind: ClaimKind
    status: ClaimStatus
    submitted_at: datetime | None


class ClaimForClaimantResponse(BaseModel):
    """CLM-07 — what the person who filed sees about their own claim."""

    reference: str
    tracking_code: str
    kind: ClaimKind
    status: ClaimStatus
    submitted_at: datetime | None
    decided_at: datetime | None
    #: Present only once approved, and never for a driver (SEC-07).
    compensation: MoneyResponse | None = None
    rejection_reason: RejectionReason | None = None
    rejection_note: str | None = None
    message_count: int = 0


class PostMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)
    attachments: list[EvidenceRef] = Field(default_factory=list)


class MessageResponse(BaseModel):
    message_id: UUID
    author: ConversationAuthor
    body: str
    written_at: datetime
    attachments: list[EvidenceRef] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    reference: str
    messages: list[MessageResponse]


# ------------------------------------------------------------------ driver


class ReportIncidentRequest(BaseModel):
    """DRV-P25. There is no amount on this request and none on the reply."""

    model_config = ConfigDict(extra="forbid")

    kind: IncidentKind
    #: Required for every kind but a vehicle or safety issue (Driver App v8).
    tracking_code: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=4000)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DriverSummaryResponse(BaseModel):
    """SEC-07 — six fields, and none of them could hold a compensation value."""

    reference: str
    kind: str
    status: str
    tracking_code: str | None
    submitted_at: datetime | None
    resolved_at: datetime | None


class IncidentReportedResponse(DriverSummaryResponse):
    """Driver App v8 `incidentDone` — plus the one custody fact the driver needs."""

    #: "The parcel stays in your custody while the report is open."
    parcel_stays_in_custody: bool


# ------------------------------------------------------------------ review


class StartReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: CLM-03 — the reviewer's explicit assertion that they read the custody record.
    custody_records_reviewed: bool = False


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Integer minor units. A float here would drift on a three-way split.
    compensation_minor_units: int = Field(gt=0)
    currency: str = Field(default="IQD", min_length=3, max_length=3)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: RejectionReason
    note: str | None = Field(default=None, max_length=2000)


class ClaimForStaffResponse(BaseModel):
    """What support or operations sees. Carries the amount only for an actor who may."""

    reference: str
    tracking_code: str
    kind: ClaimKind
    opened_by: ClaimOpenedBy
    status: ClaimStatus
    custody_boundary: CustodyBoundary
    custody_records_reviewed: bool
    submitted_at: datetime | None
    decided_at: datetime | None
    compensation: MoneyResponse | None = None
    rejection_reason: RejectionReason | None = None
    rejection_note: str | None = None
    version: int


# ------------------------------------------------------------------ operations


class ResolveIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=2000)
    #: How an incident becomes a claim: the incident records that one exists, and the
    #: claim carries the money. The incident still has no amount on it.
    linked_claim_id: UUID | None = None


class IncidentForStaffResponse(BaseModel):
    reference: str
    kind: IncidentKind
    status: str
    tracking_code: str | None
    reported_at: datetime
    note: str | None
    resolved_at: datetime | None
    resolution_note: str | None
    linked_claim_id: UUID | None
    parcel_stays_in_custody: bool
    version: int


class ReturnsAndClaimsRowResponse(BaseModel):
    """OPS-06 — one row of the returns-and-claims view."""

    reference: str
    tracking_code: str | None
    kind: str
    status: str
    opened_by: str
    submitted_at: datetime | None
    awaiting_operations: bool
    compensation: MoneyResponse | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class ReturnsAndClaimsResponse(BaseModel):
    rows: list[ReturnsAndClaimsRowResponse]


class HeldParcelsResponse(BaseModel):
    """Which parcels must not move: "the parcel stays in your custody"."""

    tracking_codes: list[str]
