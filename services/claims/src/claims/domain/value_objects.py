"""Value objects for Claims: who may open one, what it is about, and how it ends.

Two rules from v6.3 shape almost everything here.

**p.40, a Confirmed decision reversed from v5:** HUDHUD takes full responsibility for a
parcel while it is in HUDHUD's custody, and compensates the **sender**. So a claim may be
*opened* by the sender, the receiver or the driver (p.42), and the money still goes to the
sender. Those are two different questions and the model keeps them apart.

**p.37, also Confirmed:** during an at-the-door open-box inspection the responsibility is
still HUDHUD's, and it **ends the moment the receiver takes the parcel inside to test it**.
That boundary is a single value here, because it decides whether a claim can succeed at
all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ClaimOpenedBy(StrEnum):
    """v6.3 p.42 — "a compensation claim may be opened by the sender, receiver, or driver".

    Deliberately not the same list as who gets paid. CLM-01 pays the sender whoever opened
    it, and conflating the two is how a receiver ends up compensated for a parcel they
    never paid for.
    """

    SENDER = "SENDER"
    RECEIVER = "RECEIVER"
    DRIVER = "DRIVER"
    SUPPORT = "SUPPORT"


class ClaimKind(StrEnum):
    """What the claim is about.

    The first three are Customer App v3's own words on `fileAClaim`: "For a parcel that
    was damaged, lost, or delivered with the wrong amount collected."
    """

    DAMAGED_IN_TRANSIT = "DAMAGED_IN_TRANSIT"
    LOST_PARCEL = "LOST_PARCEL"
    WRONG_COD_AMOUNT = "WRONG_COD_AMOUNT"


#: Customer App v3 `photosRequired` sits under "Damaged in transit". A damage claim with
#: no photograph cannot be assessed against the custody record, so it is refused at the
#: boundary rather than opened and then rejected for a missing attachment.
KINDS_REQUIRING_PHOTOS: frozenset[ClaimKind] = frozenset({ClaimKind.DAMAGED_IN_TRANSIT})


class ClaimStatus(StrEnum):
    """v6.3 p.42, p.43 — review the records, then approve or reject with a reason."""

    SUBMITTED = "SUBMITTED"
    #: p.42 — "Hudhud reviews scan and custody records before compensating."
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.SUBMITTED: frozenset(
        {ClaimStatus.UNDER_REVIEW, ClaimStatus.WITHDRAWN}
    ),
    # A decision is only reachable through review: CLM-03 makes reading the custody
    # record a step, not a formality that can be skipped when someone is in a hurry.
    ClaimStatus.UNDER_REVIEW: frozenset(
        {ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.WITHDRAWN}
    ),
    ClaimStatus.APPROVED: frozenset(),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.WITHDRAWN: frozenset(),
}

#: Statuses in which the claimant is still waiting to hear.
OPEN_STATUSES: frozenset[ClaimStatus] = frozenset(
    {ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW}
)


class CustodyBoundary(StrEnum):
    """v6.3 p.37 — where HUDHUD's responsibility for a parcel stopped.

    The Customer App says the same thing to the receiver in `openBoxBody`: "You may open
    and inspect the parcel in front of the courier. If you take it inside to test it,
    HUDHUD's liability for damage ends there."
    """

    #: Still in HUDHUD's hands — the normal case for a claim.
    IN_HUDHUD_CUSTODY = "IN_HUDHUD_CUSTODY"
    #: Being inspected at the door, with the courier present. Still HUDHUD's (p.37).
    OPEN_BOX_AT_THE_DOOR = "OPEN_BOX_AT_THE_DOOR"
    #: Taken inside to test. HUDHUD's liability ended here.
    TAKEN_INSIDE_TO_TEST = "TAKEN_INSIDE_TO_TEST"
    #: Accepted sealed and the courier has gone.
    ACCEPTED_AND_COURIER_LEFT = "ACCEPTED_AND_COURIER_LEFT"


#: The boundaries at which HUDHUD still carries the risk. Written as the *positive* set
#: so that a new boundary added later defaults to "not our liability" and has to be added
#: here deliberately.
HUDHUD_STILL_LIABLE: frozenset[CustodyBoundary] = frozenset(
    {CustodyBoundary.IN_HUDHUD_CUSTODY, CustodyBoundary.OPEN_BOX_AT_THE_DOOR}
)


class IncidentKind(StrEnum):
    """DRV-P25 — the five the Driver App offers, verbatim from its own list."""

    DAMAGE_AFTER_ACCEPTANCE = "DAMAGE_AFTER_ACCEPTANCE"
    PARCEL_MISSING = "PARCEL_MISSING"
    LABEL_UNREADABLE = "LABEL_UNREADABLE"
    VEHICLE_OR_SAFETY_ISSUE = "VEHICLE_OR_SAFETY_ISSUE"
    HANDOFF_MISMATCH = "HANDOFF_MISMATCH"


#: Driver App v8: `needsParcel = s.incidentType !== 'Vehicle or safety issue'`. A
#: breakdown is about the driver, not about any one parcel.
INCIDENTS_ABOUT_A_PARCEL: frozenset[IncidentKind] = frozenset(
    set(IncidentKind) - {IncidentKind.VEHICLE_OR_SAFETY_ISSUE}
)


class IncidentStatus(StrEnum):
    """OPS-07 — operations resolves these; the driver only reports."""

    SUBMITTED = "SUBMITTED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    RESOLVED = "RESOLVED"


INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.SUBMITTED: frozenset(
        {IncidentStatus.UNDER_INVESTIGATION, IncidentStatus.RESOLVED}
    ),
    IncidentStatus.UNDER_INVESTIGATION: frozenset({IncidentStatus.RESOLVED}),
    IncidentStatus.RESOLVED: frozenset(),
}


class ConversationAuthor(StrEnum):
    """CLM-07 — who wrote a message on the claim's support thread."""

    CLAIMANT = "CLAIMANT"
    SUPPORT = "SUPPORT"


class RejectionReason(StrEnum):
    """v6.3 p.43 — a rejection is always documented, never bare."""

    #: p.37 — the receiver took it inside to test it.
    LIABILITY_ENDED_AT_THE_DOOR = "LIABILITY_ENDED_AT_THE_DOOR"
    #: p.42 — the scan and custody records do not support the claim.
    CUSTODY_RECORD_DOES_NOT_SUPPORT_IT = "CUSTODY_RECORD_DOES_NOT_SUPPORT_IT"
    NO_EVIDENCE_PROVIDED = "NO_EVIDENCE_PROVIDED"
    DAMAGE_PRESENT_BEFORE_PICKUP = "DAMAGE_PRESENT_BEFORE_PICKUP"
    DUPLICATE_OF_ANOTHER_CLAIM = "DUPLICATE_OF_ANOTHER_CLAIM"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class EvidenceMediaRef:
    """A pointer to stored evidence. Claims never holds the bytes."""

    bucket: str
    key: str
    content_type: str | None = None


_CLAIM_REFERENCE = re.compile(r"^CLM-\d{8}-\d{6}$")
_TRACKING_CODE = re.compile(r"^SHP-\d{8}-\d{6}$")


def is_valid_claim_reference(value: str) -> bool:
    """``CLM-20260802-000003`` — the shape Customer App v3 shows in `claimSubmitted`."""
    return bool(_CLAIM_REFERENCE.match(value))


def is_valid_tracking_code(value: str) -> bool:
    return bool(_TRACKING_CODE.match(value))


def build_claim_reference(*, day: str, sequence: int) -> str:
    """The reference the claimant is given, and the only identifier they ever see."""
    return f"CLM-{day}-{sequence:06d}"
