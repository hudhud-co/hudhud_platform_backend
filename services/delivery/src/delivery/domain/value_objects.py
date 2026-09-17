"""Value objects for the last mile: verification, inspection, payment and outcomes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class StopStatus(StrEnum):
    """One parcel's journey from the manifest to the door.

    ``ASSIGNED`` becomes ``IN_CUSTODY`` at the driver's manifest scan — v6.3 p.26 is
    explicit that "that scan transfers custody". Everything after the door is terminal:
    a stop is delivered, refused, or a failed attempt, and a same-visit reattempt is out
    of scope (Driver App v8 ``lmFailed``).
    """

    ASSIGNED = "ASSIGNED"
    IN_CUSTODY = "IN_CUSTODY"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    WAITING = "WAITING"
    AUTHORIZED = "AUTHORIZED"
    DELIVERED = "DELIVERED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


STOP_TRANSITIONS: dict[StopStatus, frozenset[StopStatus]] = {
    StopStatus.ASSIGNED: frozenset({StopStatus.IN_CUSTODY}),
    StopStatus.IN_CUSTODY: frozenset({StopStatus.EN_ROUTE, StopStatus.FAILED}),
    StopStatus.EN_ROUTE: frozenset({StopStatus.ARRIVED, StopStatus.FAILED}),
    StopStatus.ARRIVED: frozenset(
        {StopStatus.WAITING, StopStatus.AUTHORIZED, StopStatus.FAILED, StopStatus.REFUSED}
    ),
    StopStatus.WAITING: frozenset(
        {StopStatus.AUTHORIZED, StopStatus.FAILED, StopStatus.REFUSED}
    ),
    StopStatus.AUTHORIZED: frozenset(
        {StopStatus.DELIVERED, StopStatus.REFUSED, StopStatus.FAILED}
    ),
    StopStatus.DELIVERED: frozenset(),
    StopStatus.REFUSED: frozenset(),
    StopStatus.FAILED: frozenset(),
}

#: Statuses at which the parcel is physically with the driver. v6.3 p.26: custody passes
#: to the receiver only on delivery; a refusal or a failed attempt leaves it with HUDHUD.
IN_DRIVER_CUSTODY: frozenset[StopStatus] = frozenset(
    {
        StopStatus.IN_CUSTODY,
        StopStatus.EN_ROUTE,
        StopStatus.ARRIVED,
        StopStatus.WAITING,
        StopStatus.AUTHORIZED,
        StopStatus.REFUSED,
        StopStatus.FAILED,
    }
)


#: The three ways a door visit ends. Nothing follows any of them: a reattempt is a new
#: stop, not a continuation of this one.
CLOSED_STATUSES: frozenset[StopStatus] = frozenset(
    {StopStatus.DELIVERED, StopStatus.REFUSED, StopStatus.FAILED}
)


class VerificationMethod(StrEnum):
    """How the person at the door was verified (v6.3 p.26).

    ``DELIVERY_CODE`` is the normal path and works for **anyone** holding the code.
    ``NAMED_RECEIVER_ID`` is the fallback and works only for the receiver the merchant
    named — "Anyone else cannot take this parcel without the code."
    """

    DELIVERY_CODE = "DELIVERY_CODE"
    NAMED_RECEIVER_ID = "NAMED_RECEIVER_ID"


class VerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    CODE_MISMATCH = "CODE_MISMATCH"
    ID_MISMATCH = "ID_MISMATCH"
    NO_CODE_AND_NO_VALID_ID = "NO_CODE_AND_NO_VALID_ID"


class SealCheckOutcome(StrEnum):
    """The **parcel-level** seal from the merchant's packaging add-on (v6.3 p.14).

    Driver App v8 ``lmSeal`` says so in as many words: "Parcel-level seal, not the hub
    batch seal." The hub's optional batch seal is Hub's to check (v6.3 p.25).
    """

    INTACT = "INTACT"
    BROKEN = "BROKEN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class InspectionOutcome(StrEnum):
    """What happened when the parcel reached the receiver's hands.

    ``SEALED_ACCEPTED`` is the default path: v6.3 p.35 has the receiver accept or refuse
    sealed unless the merchant enabled open-box.
    """

    SEALED_ACCEPTED = "SEALED_ACCEPTED"
    OPEN_BOX_KEPT = "OPEN_BOX_KEPT"
    OPEN_BOX_REFUSED = "OPEN_BOX_REFUSED"


class PaymentMethod(StrEnum):
    """v6.3 p.31 — prepaid confirmation, cash, or a card on the POS."""

    PREPAID = "PREPAID"
    CASH = "CASH"
    POS_CARD = "POS_CARD"


class PaymentOutcome(StrEnum):
    NOTHING_TO_COLLECT = "NOTHING_TO_COLLECT"
    COLLECTED = "COLLECTED"
    DECLINED = "DECLINED"


class RefusalReason(StrEnum):
    """Optional — Driver App v8 ``lmRefuse``: "Reason is optional"."""

    CHANGED_MIND = "CHANGED_MIND"
    WRONG_ITEM = "WRONG_ITEM"
    LOOKS_DAMAGED = "LOOKS_DAMAGED"


class FailureReason(StrEnum):
    """v6.3 p.28 — the three ways an attempt ends without a handover."""

    ABSENT_AFTER_WAIT = "ABSENT_AFTER_WAIT"
    VERIFICATION_NOT_COMPLETED = "VERIFICATION_NOT_COMPLETED"
    RECEIVER_REFUSED = "RECEIVER_REFUSED"


class NextAttemptDecision(StrEnum):
    """OPS-08 — operations decides what happens after a failed attempt.

    v6.3 p.29 replaced the three-attempt limit with a three-day hold: an undelivered
    parcel is held, and after the hold it returns to the merchant.
    """

    RETRY = "RETRY"
    HOLD_AT_HUB = "HOLD_AT_HUB"
    RETURN_TO_MERCHANT = "RETURN_TO_MERCHANT"


class PhotoStage(StrEnum):
    """v6.3 p.14 — one photo before the receiver opens it, one after the inspection."""

    BEFORE_OPENING = "BEFORE_OPENING"
    AFTER_INSPECTION = "AFTER_INSPECTION"


class IssueSource(StrEnum):
    """Who raised an issue against a parcel.

    The receiver's report is CUS-12; the driver's is DRV-L21, raised from the stop.
    Both land in the same record because Delivery treats them the same way: it writes
    down what was reported and lets Claims decide whether it becomes a claim.
    """

    RECEIVER = "RECEIVER"
    DRIVER = "DRIVER"


class IssueKind(StrEnum):
    """What can be reported about a parcel (CUS-12, DRV-L21)."""

    DAMAGED = "DAMAGED"
    WRONG_AMOUNT = "WRONG_AMOUNT"
    WRONG_PARCEL = "WRONG_PARCEL"
    MISSING_ITEMS = "MISSING_ITEMS"
    COURIER_ISSUE = "COURIER_ISSUE"
    #: DRV-L21 — the driver's side: the parcel was damaged or lost in their custody.
    DAMAGED_IN_CUSTODY = "DAMAGED_IN_CUSTODY"
    LOST_IN_CUSTODY = "LOST_IN_CUSTODY"
    OTHER = "OTHER"


class RatingTag(StrEnum):
    """Tags a customer can attach to a courier rating (CUS-13)."""

    POLITE = "POLITE"
    ON_TIME = "ON_TIME"
    CAREFUL_WITH_PARCEL = "CAREFUL_WITH_PARCEL"
    CLEAR_COMMUNICATION = "CLEAR_COMMUNICATION"
    LATE = "LATE"
    RUSHED = "RUSHED"
    UNPROFESSIONAL = "UNPROFESSIONAL"


#: v6.3 p.26 and Customer App v3 `waitRuleBody`: "Couriers will wait a maximum of
#: 10 minutes at the location before rescheduling." Both sources agree, so this is a
#: constant rather than configuration.
DOOR_WAIT_SECONDS = 600

#: v6.3 p.29, recorded there as a **Confirmed decision**: "the three-attempt limit is
#: replaced by a three-day hold", after which an undelivered parcel returns to the
#: merchant. The number is settled in the source, so it is a constant here and not
#: configuration — unlike the lateness thresholds, which v6.3 leaves open.
HOLD_DAYS = 3

#: How many times a code may be entered before the attempt must end another way. v6.3
#: fixes no number; this is the fail-closed default and is configuration.
DEFAULT_MAX_CODE_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("-90") <= self.latitude <= Decimal("90")):
            msg = "latitude must be between -90 and 90"
            raise ValueError(msg)
        if not (Decimal("-180") <= self.longitude <= Decimal("180")):
            msg = "longitude must be between -180 and 180"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A handover window the receiver asked for (CUS-11, v6.3 p.20)."""

    starts_at_hour: int
    ends_at_hour: int

    def __post_init__(self) -> None:
        if not (0 <= self.starts_at_hour <= 23 and 1 <= self.ends_at_hour <= 24):
            msg = "a handover window must lie within a day"
            raise ValueError(msg)
        if self.ends_at_hour <= self.starts_at_hour:
            msg = "a handover window must end after it starts"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class EvidenceMediaRef:
    """An opaque pointer to stored evidence. Delivery never holds the bytes."""

    bucket: str
    key: str
    content_type: str | None = None


_TRACKING_CODE = re.compile(r"^SHP-\d{8}-\d{6}$")
_POS_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{3,31}$")


def is_tracking_code(value: str) -> bool:
    return bool(_TRACKING_CODE.match(value))


def normalize_pos_reference(raw: str) -> str:
    candidate = raw.strip()
    if not _POS_REFERENCE.match(candidate):
        msg = f"not a usable POS reference: {raw}"
        raise ValueError(msg)
    return candidate
