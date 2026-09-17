"""Value objects for driver onboarding, shift patterns, attendance and leave."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time
from enum import IntEnum, StrEnum


class ApplicationStatus(StrEnum):
    """A driver applicant's progress (Driver App v8 ``reg1``–``reg4``).

    ``AWAITING_OFFICE_VERIFICATION`` is the status the app calls "Pending verification",
    and it is the whole point of this aggregate: v6.3 and the app both require a physical
    check of identity, vehicle and documents at a HUDHUD office **before tasks can be
    assigned**. A remote submission alone never makes someone assignable.
    """

    SUBMITTED = "SUBMITTED"
    AWAITING_OFFICE_VERIFICATION = "AWAITING_OFFICE_VERIFICATION"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.SUBMITTED: frozenset(
        {
            ApplicationStatus.AWAITING_OFFICE_VERIFICATION,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.AWAITING_OFFICE_VERIFICATION: frozenset(
        {
            ApplicationStatus.VERIFIED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.VERIFIED: frozenset(),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.WITHDRAWN: frozenset(),
}


class DriverStatus(StrEnum):
    """A driver's standing, separate from whether they may be given work today.

    A lateness block pauses assignment without changing this: the app is explicit that
    "Assignment is paused until support clears the record", not that the account is
    closed.
    """

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    OFFBOARDED = "OFFBOARDED"


class Weekday(IntEnum):
    """ISO weekday, so a pattern can be compared with `date.isoweekday()` directly."""

    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 3
    THURSDAY = 4
    FRIDAY = 5
    SATURDAY = 6
    SUNDAY = 7


class ShiftSlot(StrEnum):
    """The shift windows a driver picks (Driver App v8 ``regHours``).

    "Pick every shift you can work on each day — choose as many as you like."
    """

    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"


class AttendanceStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    STARTED = "STARTED"
    MISSED = "MISSED"
    ON_LEAVE = "ON_LEAVE"


class LeaveReason(StrEnum):
    """The four reasons the app offers (Driver App v8 ``leave``)."""

    SICK = "SICK"
    FAMILY_EMERGENCY = "FAMILY_EMERGENCY"
    VEHICLE_PROBLEM = "VEHICLE_PROBLEM"
    PERSONAL = "PERSONAL"


class LeaveKind(StrEnum):
    FULL_DAY = "FULL_DAY"
    HOURLY = "HOURLY"


class LeaveStatus(StrEnum):
    """A leave request's life.

    ``PENDING`` matters as much as the outcome: "no lateness penalty applies while your
    request is pending" (Driver App v8 ``leave``), so a request under review protects the
    driver before anyone has decided anything.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


LEAVE_TRANSITIONS: dict[LeaveStatus, frozenset[LeaveStatus]] = {
    LeaveStatus.PENDING: frozenset(
        {LeaveStatus.APPROVED, LeaveStatus.DECLINED, LeaveStatus.WITHDRAWN}
    ),
    LeaveStatus.APPROVED: frozenset(),
    LeaveStatus.DECLINED: frozenset(),
    LeaveStatus.WITHDRAWN: frozenset(),
}


class BlockReason(StrEnum):
    LATE_START = "LATE_START"
    OPERATIONS_HOLD = "OPERATIONS_HOLD"


class IneligibilityReason(StrEnum):
    """Why a driver may not be given work right now.

    Returned to Pickup and Delivery so they can say *why* rather than only refusing.
    """

    NO_DRIVER_PROFILE = "NO_DRIVER_PROFILE"
    #: SEC-03 — the physical office check has not happened yet.
    OFFICE_VERIFICATION_PENDING = "OFFICE_VERIFICATION_PENDING"
    SUSPENDED = "SUSPENDED"
    OFFBOARDED = "OFFBOARDED"
    #: DRV-A02 — paused, not closed. Support can clear it in minutes.
    LATENESS_BLOCK = "LATENESS_BLOCK"
    ON_APPROVED_LEAVE = "ON_APPROVED_LEAVE"
    NO_SHIFT_TODAY = "NO_SHIFT_TODAY"
    SHIFT_NOT_STARTED = "SHIFT_NOT_STARTED"


#: Documents the office check confirms (Driver App v8 ``reg4`` banner). Recorded as the
#: checklist the operator works through, not invented policy: the app lists exactly these.
REQUIRED_OFFICE_DOCUMENTS: tuple[str, ...] = (
    "ID_CARD",
    "DRIVING_LICENCE",
    "VEHICLE_REGISTRATION",
    "INSURANCE_CERTIFICATE",
)

_REFERENCE = re.compile(r"^(APP|LV)-\d{4}-\d{4}$")
_PLATE = re.compile(r"^[A-Z0-9][A-Z0-9 -]{2,15}$")


@dataclass(frozen=True, slots=True)
class ShiftWindow:
    """One workable window on one weekday."""

    weekday: Weekday
    slot: ShiftSlot
    starts_at: time
    ends_at: time

    def __post_init__(self) -> None:
        if self.ends_at <= self.starts_at:
            msg = "a shift window must end after it starts"
            raise ValueError(msg)

    def covers(self, moment: time) -> bool:
        return self.starts_at <= moment < self.ends_at


@dataclass(frozen=True, slots=True)
class VehicleDetails:
    """What the applicant declares and the office verifies."""

    kind: str
    plate_number: str
    model: str | None = None

    def __post_init__(self) -> None:
        if not _PLATE.match(self.plate_number.strip().upper()):
            msg = f"not a usable plate number: {self.plate_number}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class LeaveWindow:
    """When leave applies. Full-day leave spans dates; hourly leave sits inside one."""

    kind: LeaveKind
    start_date: date
    end_date: date
    starts_at: time | None = None
    ends_at: time | None = None

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            msg = "leave cannot end before it starts"
            raise ValueError(msg)
        if self.kind is LeaveKind.HOURLY:
            if self.starts_at is None or self.ends_at is None:
                msg = "hourly leave must state the hours"
                raise ValueError(msg)
            if self.end_date != self.start_date:
                msg = "hourly leave sits inside a single day"
                raise ValueError(msg)
            if self.ends_at <= self.starts_at:
                msg = "hourly leave must end after it starts"
                raise ValueError(msg)
        elif self.starts_at is not None or self.ends_at is not None:
            msg = "full-day leave does not carry hours"
            raise ValueError(msg)

    def covers_date(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    def covers(self, day: date, moment: time | None = None) -> bool:
        if not self.covers_date(day):
            return False
        if self.kind is LeaveKind.FULL_DAY:
            return True
        if moment is None:
            return True
        assert self.starts_at is not None and self.ends_at is not None
        return self.starts_at <= moment < self.ends_at


def validate_reference(value: str) -> str:
    candidate = value.strip().upper()
    if not _REFERENCE.match(candidate):
        msg = f"invalid reference: {value}"
        raise ValueError(msg)
    return candidate


def normalize_plate(value: str) -> str:
    candidate = value.strip().upper()
    if not _PLATE.match(candidate):
        msg = f"not a usable plate number: {value}"
        raise ValueError(msg)
    return candidate
