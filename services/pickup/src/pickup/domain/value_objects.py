"""Value objects for Pickup recovery and acceptance lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PickupTaskStatus(StrEnum):
    """Pickup task lifecycle status including recovery terminal states.

    ``PENDING`` covers created/assigned work the driver has not progressed yet.
    ``ARRIVED`` and ``SCANNED`` are coordination and identity evidence only — neither
    starts custody, SLA, or acceptance. ``FAILED`` is a completed attempt that recovery
    may still supersede; only ``SUPERSEDED`` and ``CANCELLED`` close the lineage.
    """

    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    SCANNED = "SCANNED"
    PROOF_CAPTURED = "PROOF_CAPTURED"
    EXCEPTION_REPORTED = "EXCEPTION_REPORTED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


#: Statuses from which a driver may still progress the task forward.
ACTIVE_PICKUP_TASK_STATUSES: frozenset[PickupTaskStatus] = frozenset(
    {
        PickupTaskStatus.PENDING,
        PickupTaskStatus.ARRIVED,
        PickupTaskStatus.SCANNED,
        PickupTaskStatus.PROOF_CAPTURED,
        PickupTaskStatus.EXCEPTION_REPORTED,
    }
)

#: Ordered forward-only driver progression. Backward moves are rejected.
PICKUP_TASK_PROGRESSION: tuple[PickupTaskStatus, ...] = (
    PickupTaskStatus.PENDING,
    PickupTaskStatus.ARRIVED,
    PickupTaskStatus.SCANNED,
    PickupTaskStatus.PROOF_CAPTURED,
)


class AssignmentState(StrEnum):
    """Driver-side response to a pickup assignment offer."""

    OFFERED = "OFFERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"


class AssignmentDeclineReason(StrEnum):
    VEHICLE_ISSUE = "VEHICLE_ISSUE"
    OVER_CAPACITY = "OVER_CAPACITY"
    SAFETY_CONCERN = "SAFETY_CONCERN"
    TOO_FAR = "TOO_FAR"
    SESSION_ENDING = "SESSION_ENDING"
    PERSONAL_EMERGENCY = "PERSONAL_EMERGENCY"
    OTHER = "OTHER"


class PickupExceptionReason(StrEnum):
    """Driver-reported pickup exception reasons (factual report only)."""

    SENDER_ABSENT = "SENDER_ABSENT"
    WRONG_ADDRESS = "WRONG_ADDRESS"
    CANNOT_CONTACT_SENDER = "CANNOT_CONTACT_SENDER"
    PACKAGE_NOT_READY = "PACKAGE_NOT_READY"
    DAMAGED_PACKAGE = "DAMAGED_PACKAGE"
    POOR_PACKAGING = "POOR_PACKAGING"
    MISSING_LABEL = "MISSING_LABEL"
    CODE_MISMATCH = "CODE_MISMATCH"
    UNSAFE_LOCATION = "UNSAFE_LOCATION"
    SENDER_REFUSED_HANDOVER = "SENDER_REFUSED_HANDOVER"
    ACCESS_BLOCKED = "ACCESS_BLOCKED"
    OTHER = "OTHER"


#: Reasons whose report is only accepted with a contact attempt on record.
CONTACT_ATTEMPT_REQUIRED_REASONS: frozenset[PickupExceptionReason] = frozenset(
    {PickupExceptionReason.CANNOT_CONTACT_SENDER}
)

#: Reasons whose report is only accepted with free-text notes or condition proof.
EVIDENCE_REQUIRED_REASONS: frozenset[PickupExceptionReason] = frozenset(
    {
        PickupExceptionReason.SENDER_ABSENT,
        PickupExceptionReason.DAMAGED_PACKAGE,
        PickupExceptionReason.POOR_PACKAGING,
        PickupExceptionReason.MISSING_LABEL,
        PickupExceptionReason.UNSAFE_LOCATION,
        PickupExceptionReason.OTHER,
    }
)


class PackageConditionStatus(StrEnum):
    """Condition recorded on the Pickup condition proof."""

    GOOD = "GOOD"
    MINOR_DAMAGE = "MINOR_DAMAGE"
    MAJOR_DAMAGE = "MAJOR_DAMAGE"
    REPACKAGED = "REPACKAGED"


class PackagingAssessment(StrEnum):
    """Driver judgement of the packaging at the door — packaging only, never contents.

    The four outcomes are the product's own (Driver App v8 ``condition`` screen; v6.3 p.16).
    ``TOO_WEAK`` exists so that "will not survive handling" is expressible as a state that
    cannot be accepted, rather than as a note on an accepted parcel.
    """

    GOOD = "GOOD"
    BORDERLINE = "BORDERLINE"
    TOO_WEAK = "TOO_WEAK"
    PRE_EXISTING_DAMAGE = "PRE_EXISTING_DAMAGE"


class ConditionDecision(StrEnum):
    """What the driver decided to do about the packaging they just judged.

    A warning and a note are both *evidence recorded on the shipment*, never a commercial
    or compensation decision — that authority belongs outside the field (v6.3 p.16, p.18).
    """

    ACCEPT = "ACCEPT"
    ACCEPT_WITH_WARNING = "ACCEPT_WITH_WARNING"
    ACCEPT_WITH_NOTE = "ACCEPT_WITH_NOTE"
    REFUSE = "REFUSE"


#: The only decisions each assessment permits. ``TOO_WEAK`` can be refused and nothing else:
#: "Too weak packaging can only be refused" (Driver App v8 ``condition`` hint).
PERMITTED_CONDITION_DECISIONS: dict[PackagingAssessment, frozenset[ConditionDecision]] = {
    PackagingAssessment.GOOD: frozenset({ConditionDecision.ACCEPT}),
    PackagingAssessment.BORDERLINE: frozenset(
        {ConditionDecision.ACCEPT_WITH_WARNING, ConditionDecision.REFUSE}
    ),
    PackagingAssessment.TOO_WEAK: frozenset({ConditionDecision.REFUSE}),
    PackagingAssessment.PRE_EXISTING_DAMAGE: frozenset(
        {ConditionDecision.ACCEPT_WITH_NOTE, ConditionDecision.REFUSE}
    ),
}

#: Decision assumed when a caller supplies an assessment but no explicit decision.
#: Only defined where the assessment leaves the driver no choice to express.
DEFAULT_CONDITION_DECISION: dict[PackagingAssessment, ConditionDecision] = {
    PackagingAssessment.GOOD: ConditionDecision.ACCEPT,
    PackagingAssessment.BORDERLINE: ConditionDecision.ACCEPT_WITH_WARNING,
    PackagingAssessment.TOO_WEAK: ConditionDecision.REFUSE,
    PackagingAssessment.PRE_EXISTING_DAMAGE: ConditionDecision.ACCEPT_WITH_NOTE,
}

#: Backward-compatible derivation for callers that predate ``packaging_assessment``.
#: Existing rows and existing clients keep their exact behaviour.
CONDITION_STATUS_TO_ASSESSMENT: dict[str, PackagingAssessment] = {
    "GOOD": PackagingAssessment.GOOD,
    "REPACKAGED": PackagingAssessment.GOOD,
    "MINOR_DAMAGE": PackagingAssessment.PRE_EXISTING_DAMAGE,
    "MAJOR_DAMAGE": PackagingAssessment.PRE_EXISTING_DAMAGE,
}


class StopOutcome(StrEnum):
    """Terminal non-custody outcomes for one expected parcel at a merchant stop.

    Neither outcome starts custody and neither publishes anything: "The parcel stays with
    the merchant. No custody event was recorded." (Driver App v8 ``notAccepted``).
    """

    REFUSED = "REFUSED"
    NOT_PRESENTED = "NOT_PRESENTED"


class PickupRefusalReason(StrEnum):
    """Why the driver refused a parcel at the door."""

    TOO_WEAK_PACKAGING = "TOO_WEAK_PACKAGING"
    DAMAGED_BEFORE_PICKUP = "DAMAGED_BEFORE_PICKUP"
    DOES_NOT_MATCH_SHIPMENT = "DOES_NOT_MATCH_SHIPMENT"


#: Refusal reason implied by an assessment, when the driver refuses straight from the
#: condition check rather than choosing a reason separately.
ASSESSMENT_REFUSAL_REASON: dict[PackagingAssessment, PickupRefusalReason] = {
    PackagingAssessment.TOO_WEAK: PickupRefusalReason.TOO_WEAK_PACKAGING,
    PackagingAssessment.BORDERLINE: PickupRefusalReason.TOO_WEAK_PACKAGING,
    PackagingAssessment.PRE_EXISTING_DAMAGE: PickupRefusalReason.DAMAGED_BEFORE_PICKUP,
}


class ScanResolutionOutcome(StrEnum):
    """Classification of one scanned label against the driver's current stop.

    Resolution is read-only. An unregistered label must never be able to become a shipment
    in the field, and an unreadable label may never be typed in or replaced
    (Driver App v8 ``scanEx:unknown`` / ``scanEx:unreadable``).
    """

    VALID = "VALID"
    UNKNOWN_LABEL = "UNKNOWN_LABEL"
    WRONG_MERCHANT = "WRONG_MERCHANT"
    NOT_IN_THIS_PICKUP = "NOT_IN_THIS_PICKUP"
    ALREADY_ACCEPTED = "ALREADY_ACCEPTED"
    CANCELLED_SHIPMENT = "CANCELLED_SHIPMENT"
    DUPLICATE_SCAN = "DUPLICATE_SCAN"
    UNREADABLE = "UNREADABLE"


class PickupTaskAcceptanceState(StrEnum):
    """Pickup task acceptance outcome — aligned with Shipment W11 terminology."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_EXCEPTION = "ACCEPTED_WITH_EXCEPTION"
    REJECTED = "REJECTED"


class AcceptanceOutcome(StrEnum):
    """Custody-starting acceptance outcomes that may emit pickup.fact.accepted."""

    ACCEPTED = "ACCEPTED"
    ACCEPTED_WITH_EXCEPTION = "ACCEPTED_WITH_EXCEPTION"


class OutboxStatus(StrEnum):
    """Integration outbox row lifecycle (ADR-0008)."""

    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"


class RecoveryAction(StrEnum):
    """Distinct recovery business actions."""

    RETRY = "RETRY"
    RESCHEDULE = "RESCHEDULE"
    REASSIGN = "REASSIGN"
    CANCEL = "CANCEL"


class ShipmentStatus(StrEnum):
    """Minimal Shipment status facts for recovery eligibility — not Shipment authority."""

    CREATED = "CREATED"
    IN_CUSTODY = "IN_CUSTODY"


class CustodyType(StrEnum):
    """Custody holder type — canonical target terminology is PICKUP_DRIVER (ADR-0003 W17-A)."""

    PICKUP_DRIVER = "PICKUP_DRIVER"
    ORIGIN_HUB = "ORIGIN_HUB"


@dataclass(frozen=True, slots=True)
class ScheduledWindow:
    """Optional pickup schedule window carried across recovery attempts."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class EvidenceMediaRef:
    """External evidence pointer — never inline bytes."""

    ref_type: str
    bucket: str
    key: str
    content_type: str | None = None
