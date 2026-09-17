"""Value objects for hub intake, processing, consignments and linehaul."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from enum import StrEnum


class DropOffStatus(StrEnum):
    """A regular customer bringing a parcel to a hub (v6.3 p.18, Figure 6).

    The two entry points are both modelled: a customer who entered details in the app
    arrives ``EXPECTED``; one who "arrives at a hub without entering anything at all"
    starts at ``DETAILS_CAPTURED`` when staff take the details on the spot.

    ``ACCEPTED`` is the custody event. Until then nothing is in Hudhud's care.
    """

    EXPECTED = "EXPECTED"
    DETAILS_CAPTURED = "DETAILS_CAPTURED"
    LABELLED = "LABELLED"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


DROP_OFF_TRANSITIONS: dict[DropOffStatus, frozenset[DropOffStatus]] = {
    DropOffStatus.EXPECTED: frozenset(
        {
            DropOffStatus.DETAILS_CAPTURED,
            DropOffStatus.EXPIRED,
            DropOffStatus.CANCELLED,
        }
    ),
    DropOffStatus.DETAILS_CAPTURED: frozenset(
        {DropOffStatus.LABELLED, DropOffStatus.EXPIRED, DropOffStatus.CANCELLED}
    ),
    # A labelled parcel is physically at the counter with a sticker on it, so it can no
    # longer expire unclaimed — only be accepted or cancelled.
    DropOffStatus.LABELLED: frozenset(
        {DropOffStatus.ACCEPTED, DropOffStatus.CANCELLED}
    ),
    DropOffStatus.ACCEPTED: frozenset(),
    DropOffStatus.EXPIRED: frozenset(),
    DropOffStatus.CANCELLED: frozenset(),
}


class ParcelPresenceStatus(StrEnum):
    """Where a parcel is inside one hub (v6.3 p.22, Figure 8)."""

    RECEIVED = "RECEIVED"
    SORTED = "SORTED"
    GROUPED = "GROUPED"
    DEPARTED = "DEPARTED"
    READY_FOR_LAST_MILE = "READY_FOR_LAST_MILE"
    HANDED_TO_LAST_MILE = "HANDED_TO_LAST_MILE"
    HELD = "HELD"


class RoutingDecision(StrEnum):
    """What the origin hub decided to do with a parcel.

    v6.3 p.22: "If a parcel is only travelling within the same city, this hub-to-hub stage
    is skipped entirely, and the parcel goes straight toward last-mile delivery."
    """

    SAME_CITY_DIRECT = "SAME_CITY_DIRECT"
    INTER_CITY_LINEHAUL = "INTER_CITY_LINEHAUL"


class Urgency(StrEnum):
    """Sorting input at the origin hub — "sorted by destination city, urgency, and route"."""

    STANDARD = "STANDARD"
    URGENT = "URGENT"


class ConsignmentStatus(StrEnum):
    """A group of parcels heading the same way (v6.3 p.22)."""

    OPEN = "OPEN"
    SEALED = "SEALED"
    DISPATCHED = "DISPATCHED"
    ARRIVED = "ARRIVED"
    RECONCILED = "RECONCILED"
    #: A seal that did not match on arrival. Never a silent pass-through (p.25).
    UNDER_TAMPER_INVESTIGATION = "UNDER_TAMPER_INVESTIGATION"


CONSIGNMENT_TRANSITIONS: dict[ConsignmentStatus, frozenset[ConsignmentStatus]] = {
    ConsignmentStatus.OPEN: frozenset(
        {ConsignmentStatus.SEALED, ConsignmentStatus.DISPATCHED}
    ),
    ConsignmentStatus.SEALED: frozenset({ConsignmentStatus.DISPATCHED}),
    ConsignmentStatus.DISPATCHED: frozenset({ConsignmentStatus.ARRIVED}),
    ConsignmentStatus.ARRIVED: frozenset(
        {ConsignmentStatus.RECONCILED, ConsignmentStatus.UNDER_TAMPER_INVESTIGATION}
    ),
    ConsignmentStatus.RECONCILED: frozenset(),
    # Terminal here: resolving a tamper investigation is an Operations and Claims matter,
    # not something a hub clears by scanning again.
    ConsignmentStatus.UNDER_TAMPER_INVESTIGATION: frozenset(),
}


class SealCheckOutcome(StrEnum):
    """v6.3 p.25 — a mismatch opens an investigation, never a silent pass-through."""

    INTACT = "INTACT"
    MISMATCHED = "MISMATCHED"
    MISSING = "MISSING"
    BROKEN = "BROKEN"


#: Outcomes that must stop the parcel and open an investigation.
TAMPER_OUTCOMES: frozenset[SealCheckOutcome] = frozenset(
    {SealCheckOutcome.MISMATCHED, SealCheckOutcome.MISSING, SealCheckOutcome.BROKEN}
)


class LinehaulStatus(StrEnum):
    PLANNED = "PLANNED"
    DEPARTED = "DEPARTED"
    ARRIVED = "ARRIVED"
    CANCELLED = "CANCELLED"


class PositionSource(StrEnum):
    """v6.3 p.24 keeps two independent sources on purpose.

    "The transport vehicle itself is tracked. The driver's own device provides a second,
    independent way of confirming the vehicle's location." Recording which source a fix
    came from is what makes the second one worth having.
    """

    VEHICLE_TRACKER = "VEHICLE_TRACKER"
    DRIVER_DEVICE = "DRIVER_DEVICE"


class HoldReason(StrEnum):
    """Why a parcel is stopped inside a hub rather than moving on."""

    TAMPER_INVESTIGATION = "TAMPER_INVESTIGATION"
    CHECKPOINT_INTERCEPTION = "CHECKPOINT_INTERCEPTION"
    DAMAGED_IN_HUB = "DAMAGED_IN_HUB"
    MISSING_AT_RECONCILIATION = "MISSING_AT_RECONCILIATION"
    OPERATIONS_HOLD = "OPERATIONS_HOLD"


class ParcelDisposition(StrEnum):
    """What must happen to a held parcel.

    ``RETURN_TO_MERCHANT`` is not a judgement call: v6.3 p.24 Confirmed decision — "If a
    checkpoint along an inter-city route opens a parcel, Hudhud returns that parcel to the
    merchant."
    """

    CONTINUE = "CONTINUE"
    RETURN_TO_MERCHANT = "RETURN_TO_MERCHANT"
    AWAIT_OPERATIONS = "AWAIT_OPERATIONS"


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
class CutOffTime:
    """A hub's daily cut-off (v6.3 p.23, CHANGED IN V6.3).

    "This cut-off time is set per hub, not company-wide — different hubs can have
    different cut-off times." Modelling it per hub is the requirement; a shared constant
    would quietly reintroduce the company-wide rule v6.3 replaced.
    """

    local_time: time

    def has_passed(self, moment_local: time) -> bool:
        return moment_local >= self.local_time


GOVERNORATES: frozenset[str] = frozenset(
    {
        "BAGHDAD", "BASRA", "NINEVEH", "ERBIL", "SULAYMANIYAH", "DUHOK", "KIRKUK",
        "NAJAF", "KARBALA", "BABIL", "WASIT", "MAYSAN", "DHI_QAR", "MUTHANNA",
        "QADISIYYAH", "DIYALA", "ANBAR", "SALAH_AL_DIN", "HALABJA",
    }
)

_HUB_CODE = re.compile(r"^[A-Z0-9]{2,12}$")
_LABEL_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,31}$")
_SEAL_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,31}$")


def normalize_governorate(raw: str) -> str:
    token = raw.strip().upper().replace(" ", "_").replace("-", "_")
    if token not in GOVERNORATES:
        msg = f"unknown governorate: {raw}"
        raise ValueError(msg)
    return token


def normalize_hub_code(raw: str) -> str:
    candidate = raw.strip().upper()
    if not _HUB_CODE.match(candidate):
        msg = f"invalid hub code: {raw}"
        raise ValueError(msg)
    return candidate


def normalize_label_code(raw: str) -> str:
    candidate = raw.strip().upper()
    if not _LABEL_CODE.match(candidate):
        msg = f"not a usable label code: {raw}"
        raise ValueError(msg)
    return candidate


def normalize_seal_code(raw: str) -> str:
    candidate = raw.strip().upper()
    if not _SEAL_CODE.match(candidate):
        msg = f"not a usable seal code: {raw}"
        raise ValueError(msg)
    return candidate
