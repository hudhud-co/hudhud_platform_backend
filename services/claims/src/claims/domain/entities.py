"""Claims aggregates: the compensation claim, its support thread, and driver incidents.

The compensation amount lives on the claim, and **never leaves this service towards a
driver**. SEC-07 is a one-line requirement with a wide blast radius — Driver App v8 says
"No compensation or claim value is shown to the driver" — so the amount is kept out of
every driver-facing read model by construction rather than by remembering to filter it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from claims.domain.money import Money
from claims.domain.value_objects import (
    CLAIM_TRANSITIONS,
    HUDHUD_STILL_LIABLE,
    INCIDENT_TRANSITIONS,
    INCIDENTS_ABOUT_A_PARCEL,
    KINDS_REQUIRING_PHOTOS,
    OPEN_STATUSES,
    ClaimKind,
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    CustodyBoundary,
    EvidenceMediaRef,
    IncidentKind,
    IncidentStatus,
    RejectionReason,
)


@dataclass(slots=True)
class CompensationClaim:
    """One claim about one parcel (CLM-01 … CLM-06).

    ``compensation_amount`` is the outcome of the review, not something the claimant
    proposes. A claimant-supplied figure would be an anchor on a decision v6.3 p.42
    reserves for HUDHUD after reading the custody record.
    """

    claim_id: UUID
    reference: str
    tracking_code: str
    kind: ClaimKind
    opened_by: ClaimOpenedBy
    #: Who raised it. May be the receiver or the driver — see `compensated_principal_id`.
    opened_by_principal_id: UUID | None = None
    #: CLM-01 — the sender is compensated, whoever opened the claim.
    sender_principal_id: UUID | None = None
    merchant_id: UUID | None = None
    description: str | None = None
    evidence: tuple[EvidenceMediaRef, ...] = ()
    #: v6.3 p.37 — where HUDHUD's responsibility stopped for this parcel.
    custody_boundary: CustodyBoundary = CustodyBoundary.IN_HUDHUD_CUSTODY
    status: ClaimStatus = ClaimStatus.SUBMITTED
    submitted_at: datetime | None = None
    review_started_at: datetime | None = None
    reviewed_by_actor_id: UUID | None = None
    #: CLM-03 — what the reviewer actually read before deciding.
    custody_records_reviewed: bool = False
    decided_at: datetime | None = None
    decided_by_actor_id: UUID | None = None
    compensation_amount: Money | None = None
    rejection_reason: RejectionReason | None = None
    rejection_note: str | None = None
    withdrawn_at: datetime | None = None
    version: int = 1

    # ------------------------------------------------------------- state

    def can_transition_to(self, target: ClaimStatus) -> bool:
        return target in CLAIM_TRANSITIONS[self.status]

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_decided(self) -> bool:
        return self.status in {ClaimStatus.APPROVED, ClaimStatus.REJECTED}

    # ------------------------------------------------------------- liability

    @property
    def hudhud_is_liable(self) -> bool:
        """v6.3 p.37 — still ours at the door, not once it goes inside to be tested."""
        return self.custody_boundary in HUDHUD_STILL_LIABLE

    @property
    def requires_photographs(self) -> bool:
        """Customer App v3 `photosRequired`, shown under "Damaged in transit"."""
        return self.kind in KINDS_REQUIRING_PHOTOS

    @property
    def has_photographs(self) -> bool:
        return len(self.evidence) > 0

    @property
    def compensated_principal_id(self) -> UUID | None:
        """CLM-01 — the sender, whoever opened the claim.

        A receiver may open a claim about a parcel they were paying cash for; the money
        still goes back to the person who paid HUDHUD to carry it.
        """
        return self.sender_principal_id


@dataclass(slots=True)
class ClaimMessage:
    """One message on a claim's support thread (CLM-07).

    Customer App v3 pairs `claimNew` with the support screens: a claimant files, and then
    talks to a person. The thread belongs to the claim so the conversation and the
    decision cannot drift apart.
    """

    message_id: UUID
    claim_id: UUID
    author: ConversationAuthor
    body: str
    written_at: datetime
    author_principal_id: UUID | None = None
    attachments: tuple[EvidenceMediaRef, ...] = ()
    version: int = 1


@dataclass(slots=True)
class DriverIncident:
    """DRV-P25 — a driver reporting something about a parcel in their custody.

    Driver App v8, on the report screen: "The parcel stays in your custody while the
    report is open. No amounts are shown or decided here." Both halves are modelled:
    :attr:`parcel_stays_in_custody` is always true while open, and there is **no
    compensation field on this class at all**.
    """

    incident_id: UUID
    reference: str
    kind: IncidentKind
    reported_by_driver_id: UUID
    reported_at: datetime
    #: Every kind except a vehicle or safety issue is about one parcel.
    tracking_code: str | None = None
    note: str | None = None
    evidence: tuple[EvidenceMediaRef, ...] = ()
    status: IncidentStatus = IncidentStatus.SUBMITTED
    investigation_started_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by_actor_id: UUID | None = None
    resolution_note: str | None = None
    #: Set when operations opens a claim off the back of the incident.
    linked_claim_id: UUID | None = None
    version: int = 1

    def can_transition_to(self, target: IncidentStatus) -> bool:
        return target in INCIDENT_TRANSITIONS[self.status]

    @property
    def is_about_a_parcel(self) -> bool:
        return self.kind in INCIDENTS_ABOUT_A_PARCEL

    @property
    def is_open(self) -> bool:
        return self.status is not IncidentStatus.RESOLVED

    @property
    def parcel_stays_in_custody(self) -> bool:
        """Driver App v8 — the parcel does not move while the report is open."""
        return self.is_about_a_parcel and self.is_open


@dataclass(frozen=True, slots=True)
class ClaimSummaryForDriver:
    """What a driver may see about a claim or incident they were involved in.

    **SEC-07.** There is no amount here and no field one could be put in. That is the
    entire design: a driver-facing read model that cannot carry a value, rather than a
    full model that a careless serializer might expose.
    """

    reference: str
    kind: str
    status: str
    tracking_code: str | None
    submitted_at: datetime | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClaimSummaryForClaimant:
    """CLM-07 — what the person who filed sees.

    Carries the amount only once it has been approved, because before that there is no
    amount, and showing a proposed one would imply a decision nobody has made.
    """

    reference: str
    tracking_code: str
    kind: ClaimKind
    status: ClaimStatus
    submitted_at: datetime | None
    decided_at: datetime | None
    compensation_amount: Money | None
    rejection_reason: RejectionReason | None
    rejection_note: str | None
    message_count: int = 0


@dataclass(frozen=True, slots=True)
class ReturnsAndClaimsRow:
    """OPS-06 — one row of the operations returns-and-claims view."""

    reference: str
    tracking_code: str | None
    kind: str
    status: str
    opened_by: str
    submitted_at: datetime | None
    awaiting_operations: bool
    compensation_amount: Money | None = None
    extra: dict[str, str] = field(default_factory=dict)
