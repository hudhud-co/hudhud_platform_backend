"""May this driver be given work right now?

This is the whole reason Workforce is a service rather than a table inside Pickup: both
Pickup and Delivery need the same answer, and it depends on facts neither of them owns —
whether the office check happened (SEC-03), whether a lateness block is open (DRV-A02),
whether the driver is on leave (DRV-A03), and whether they have a shift today (DRV-A04).

The answer carries its reasons so a caller can tell the driver what to do next rather than
only refusing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from workforce.domain.entities import AssignmentEligibility
from workforce.domain.value_objects import (
    DriverStatus,
    IneligibilityReason,
    LeaveStatus,
)
from workforce.ports.repository import WorkforceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


class EligibilityService:
    def __init__(
        self, unit_of_work: WorkforceUnitOfWork, *, require_started_shift: bool = True
    ) -> None:
        self._uow = unit_of_work
        # DRV-A01 — "Your assigned pickups unlock the moment you start." Configurable
        # because a hub may want to plan a route before the driver arrives.
        self._require_started_shift = require_started_shift

    def for_principal(
        self, *, principal_id: UUID, day: date | None = None
    ) -> AssignmentEligibility:
        when = day or _now().date()
        self._uow.begin()
        try:
            driver = self._uow.drivers.find_by_principal(principal_id)
            if driver is None:
                self._uow.commit()
                return AssignmentEligibility(
                    driver_id=None,
                    principal_id=principal_id,
                    eligible=False,
                    reasons=(IneligibilityReason.NO_DRIVER_PROFILE.value,),
                )

            reasons: list[str] = []
            if driver.status is DriverStatus.PENDING_VERIFICATION:
                reasons.append(IneligibilityReason.OFFICE_VERIFICATION_PENDING.value)
            elif driver.status is DriverStatus.SUSPENDED:
                reasons.append(IneligibilityReason.SUSPENDED.value)
            elif driver.status is DriverStatus.OFFBOARDED:
                reasons.append(IneligibilityReason.OFFBOARDED.value)

            if self._uow.blocks.find_active(driver.driver_id) is not None:
                reasons.append(IneligibilityReason.LATENESS_BLOCK.value)

            leave = self._uow.leave.find_covering(driver.driver_id, when)
            if leave is not None and leave.status is LeaveStatus.APPROVED:
                reasons.append(IneligibilityReason.ON_APPROVED_LEAVE.value)

            pattern = self._uow.patterns.current_for(driver.driver_id, when)
            if pattern is None or not pattern.windows_for(when):
                reasons.append(IneligibilityReason.NO_SHIFT_TODAY.value)

            attendance = self._uow.attendance.find(driver.driver_id, when)
            shift_started = attendance is not None and attendance.is_started
            if self._require_started_shift and not shift_started:
                reasons.append(IneligibilityReason.SHIFT_NOT_STARTED.value)

            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        return AssignmentEligibility(
            driver_id=driver.driver_id,
            principal_id=principal_id,
            eligible=not reasons,
            reasons=tuple(reasons),
            shift_started=shift_started,
        )
