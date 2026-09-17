"""Workforce aggregates: application, driver, shift pattern, attendance, block, leave."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from uuid import UUID

from workforce.domain.value_objects import (
    APPLICATION_TRANSITIONS,
    LEAVE_TRANSITIONS,
    ApplicationStatus,
    AttendanceStatus,
    BlockReason,
    DriverStatus,
    LeaveReason,
    LeaveStatus,
    LeaveWindow,
    ShiftWindow,
    VehicleDetails,
    Weekday,
)


@dataclass(slots=True)
class DriverApplication:
    """A driver applicant (SEC-03, Driver App v8 ``reg1``–``reg4``).

    The aggregate exists to make one thing impossible: becoming assignable without the
    physical office check. ``VERIFIED`` is reached only through
    ``AWAITING_OFFICE_VERIFICATION``, and only an operator standing at an office can move
    it there.
    """

    application_id: UUID
    applicant_principal_id: UUID
    reference: str
    status: ApplicationStatus
    full_name: str
    vehicle: VehicleDetails
    declared_shifts: tuple[ShiftWindow, ...] = ()
    terms_version_accepted: str | None = None
    documents_received: tuple[str, ...] = ()
    documents_verified: tuple[str, ...] = ()
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    verified_by_actor_id: UUID | None = None
    verified_at_office: str | None = None
    decision_reason: str | None = None
    version: int = 1

    def can_transition_to(self, target: ApplicationStatus) -> bool:
        return target in APPLICATION_TRANSITIONS[self.status]

    @property
    def is_open(self) -> bool:
        return bool(APPLICATION_TRANSITIONS[self.status])

    @property
    def awaits_office_visit(self) -> bool:
        return self.status is ApplicationStatus.AWAITING_OFFICE_VERIFICATION


@dataclass(slots=True)
class DriverProfile:
    """A driver whose application was verified.

    Created only by verifying an application, so a driver profile cannot exist for someone
    who never went to an office.
    """

    driver_id: UUID
    principal_id: UUID
    application_id: UUID
    full_name: str
    vehicle: VehicleDetails
    status: DriverStatus = DriverStatus.PENDING_VERIFICATION
    activated_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.status is DriverStatus.ACTIVE


@dataclass(slots=True)
class ShiftPattern:
    """The shifts a driver says they can work (DRV-A04).

    ``effective_from`` carries the app's own rule: "Changes apply from tomorrow." A change
    that took effect immediately would retroactively make today's start late or early.

    A superseded pattern is closed with ``effective_to`` rather than deleted, and that end
    date is *today* — the day the change was made — so the shift a driver is part-way
    through is still the one they were told about this morning.
    """

    pattern_id: UUID
    driver_id: UUID
    windows: tuple[ShiftWindow, ...]
    effective_from: date
    effective_to: date | None = None
    version: int = 1

    @property
    def is_open_ended(self) -> bool:
        return self.effective_to is None

    def applies_on(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to

    def windows_for(self, day: date) -> tuple[ShiftWindow, ...]:
        weekday = Weekday(day.isoweekday())
        return tuple(window for window in self.windows if window.weekday is weekday)

    def earliest_start_on(self, day: date) -> time | None:
        windows = self.windows_for(day)
        return min((window.starts_at for window in windows), default=None)


@dataclass(slots=True)
class AttendanceRecord:
    """One driver's shift on one day (DRV-A01).

    "Starting a shift records your attendance." The lateness is derived from the
    difference between the scheduled start and when they actually started, and stored so
    the figures the app shows are the figures on record.
    """

    attendance_id: UUID
    driver_id: UUID
    shift_date: date
    scheduled_start: time
    status: AttendanceStatus = AttendanceStatus.SCHEDULED
    started_at: datetime | None = None
    lateness_minutes: int = 0
    version: int = 1

    @property
    def is_started(self) -> bool:
        return self.status is AttendanceStatus.STARTED

    @property
    def was_late(self) -> bool:
        return self.lateness_minutes > 0


@dataclass(slots=True)
class LatenessBlock:
    """Assignment paused after a late start (DRV-A02).

    Paused, not closed: "Assignment is paused until support clears the record." The
    driver may request an unblock, and only support or operations can clear it.
    """

    block_id: UUID
    driver_id: UUID
    reason: BlockReason
    shift_date: date
    scheduled_start: time
    opened_app_at: datetime
    delay_minutes: int
    blocked_since: datetime
    unblock_requested_at: datetime | None = None
    cleared_at: datetime | None = None
    cleared_by_actor_id: UUID | None = None
    clearing_note: str | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.cleared_at is None

    @property
    def unblock_requested(self) -> bool:
        return self.unblock_requested_at is not None


@dataclass(slots=True)
class LeaveRequest:
    """A driver telling support they cannot work (DRV-A03)."""

    leave_id: UUID
    driver_id: UUID
    reference: str
    reason: LeaveReason
    window: LeaveWindow
    status: LeaveStatus = LeaveStatus.PENDING
    note: str | None = None
    requested_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by_actor_id: UUID | None = None
    decision_note: str | None = None
    version: int = 1

    def can_transition_to(self, target: LeaveStatus) -> bool:
        return target in LEAVE_TRANSITIONS[self.status]

    @property
    def is_pending(self) -> bool:
        return self.status is LeaveStatus.PENDING

    @property
    def protects_from_lateness(self) -> bool:
        """"No lateness penalty applies while your request is pending."

        Approved leave protects too — the driver was not supposed to be working.
        """
        return self.status in {LeaveStatus.PENDING, LeaveStatus.APPROVED}


@dataclass(frozen=True, slots=True)
class AssignmentEligibility:
    """The answer Pickup and Delivery need: may this driver be given work right now?

    Carries the reasons as well as the verdict so a caller can say *why* rather than only
    refusing, which is the difference between a driver who can fix the problem and one who
    calls support.
    """

    driver_id: UUID | None
    principal_id: UUID
    eligible: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    shift_started: bool = False
