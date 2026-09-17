"""Shift patterns, starting a shift, lateness and blocks (DRV-A01, A02, A04, OPS-09).

Three rules from the Driver App live here.

* "Starting a shift records your attendance. Late starts are recorded and can temporarily
  block assignment." (``shift``)
* "You opened the app after your shift start. Assignment is paused until support clears
  the record." (``blocked``)
* "Changes apply from tomorrow." (``hoursEdit``) — a pattern change never retroactively
  makes today's start late.

A pending or approved leave request suppresses the lateness penalty, because the app
promises exactly that: "no lateness penalty applies while your request is pending".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from workforce.domain.entities import (
    AttendanceRecord,
    DriverProfile,
    LatenessBlock,
    ShiftPattern,
)
from workforce.domain.errors import (
    BlockAlreadyCleared,
    BlockNotFound,
    DriverNotFound,
    NoShiftScheduled,
    OnlySupportMayClearABlock,
    ShiftAlreadyStarted,
    ShiftSelectionRequired,
)
from workforce.domain.value_objects import (
    AttendanceStatus,
    BlockReason,
    ShiftWindow,
)
from workforce.ports.repository import WorkforceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class LatenessPolicy:
    """How much lateness is tolerated, and for how long assignment pauses.

    v6.3 fixes neither number, and the app shows only one worked example (a 52-minute
    delay), so both are configuration. A guessed threshold would penalise real drivers.
    """

    grace_minutes: int = 0
    block_after_minutes: int = 1

    def is_late(self, lateness_minutes: int) -> bool:
        return lateness_minutes > self.grace_minutes

    def should_block(self, lateness_minutes: int) -> bool:
        return lateness_minutes >= self.block_after_minutes


@dataclass(frozen=True, slots=True)
class ShiftStartResult:
    attendance: AttendanceRecord
    block: LatenessBlock | None
    #: True when a pending or approved leave request stopped a penalty being applied.
    penalty_waived_by_leave: bool = False


class AttendanceService:
    def __init__(
        self, unit_of_work: WorkforceUnitOfWork, *, policy: LatenessPolicy | None = None
    ) -> None:
        self._uow = unit_of_work
        self._policy = policy or LatenessPolicy()

    # ------------------------------------------------------------- pattern

    def set_shift_pattern(
        self,
        *,
        driver_id: UUID,
        windows: tuple[ShiftWindow, ...],
        today: date | None = None,
    ) -> ShiftPattern:
        """Replace a driver's shifts, effective tomorrow.

        Driver App v8 ``hoursEdit``: "Changes apply from tomorrow." Applying them today
        would retroactively change whether this morning's start was late.
        """
        if not windows:
            raise ShiftSelectionRequired()

        self._uow.begin()
        try:
            self._require_driver(driver_id)
            reference_day = today or _now().date()
            current = self._uow.patterns.current_for(driver_id, reference_day)
            if current is not None:
                # Closed at the end of *today*, not now: the driver is entitled to the
                # shift they were told about this morning.
                current.effective_to = reference_day
                current.version += 1
                self._uow.patterns.save(current)
            pattern = ShiftPattern(
                pattern_id=uuid4(),
                driver_id=driver_id,
                windows=windows,
                effective_from=reference_day + timedelta(days=1),
            )
            self._uow.patterns.save(pattern)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return pattern

    def current_pattern(self, *, driver_id: UUID, day: date | None = None) -> ShiftPattern | None:
        self._uow.begin()
        try:
            pattern = self._uow.patterns.current_for(driver_id, day or _now().date())
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return pattern

    # ------------------------------------------------------------- shift start

    def start_shift(
        self, *, driver_id: UUID, moment: datetime | None = None
    ) -> ShiftStartResult:
        """Record attendance, and open a block if the driver started late."""
        when = moment or _now()
        shift_date = when.date()

        self._uow.begin()
        try:
            self._require_driver(driver_id)
            pattern = self._uow.patterns.current_for(driver_id, shift_date)
            scheduled = (
                pattern.earliest_start_on(shift_date) if pattern is not None else None
            )
            if scheduled is None:
                raise NoShiftScheduled(shift_date.isoformat())

            existing = self._uow.attendance.find(driver_id, shift_date)
            if existing is not None and existing.is_started:
                raise ShiftAlreadyStarted(shift_date.isoformat())

            lateness = _lateness_minutes(scheduled, when)
            record = existing or AttendanceRecord(
                attendance_id=uuid4(),
                driver_id=driver_id,
                shift_date=shift_date,
                scheduled_start=scheduled,
            )
            record.scheduled_start = scheduled
            record.status = AttendanceStatus.STARTED
            record.started_at = when
            record.lateness_minutes = lateness
            record.version += 1
            self._uow.attendance.save(record)

            # A pending or approved leave request protects the driver: the app promises
            # "no lateness penalty applies while your request is pending".
            protecting_leave = self._uow.leave.find_covering(driver_id, shift_date)
            waived = (
                protecting_leave is not None and protecting_leave.protects_from_lateness
            )

            block: LatenessBlock | None = None
            if (
                self._policy.is_late(lateness)
                and self._policy.should_block(lateness)
                and not waived
            ):
                block = self._uow.blocks.find_active(driver_id)
                if block is None:
                    block = LatenessBlock(
                        block_id=uuid4(),
                        driver_id=driver_id,
                        reason=BlockReason.LATE_START,
                        shift_date=shift_date,
                        scheduled_start=scheduled,
                        opened_app_at=when,
                        delay_minutes=lateness,
                        blocked_since=when,
                    )
                    self._uow.blocks.save(block)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ShiftStartResult(
            attendance=record, block=block, penalty_waived_by_leave=waived
        )

    # ------------------------------------------------------------- blocks

    def request_unblock(self, *, driver_id: UUID) -> LatenessBlock:
        """The driver asks support to look — they cannot clear it themselves."""
        self._uow.begin()
        try:
            block = self._uow.blocks.find_active(driver_id)
            if block is None:
                raise BlockNotFound(str(driver_id))
            if block.unblock_requested_at is None:
                block.unblock_requested_at = _now()
                block.version += 1
                self._uow.blocks.save(block)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return block

    def clear_block(
        self,
        *,
        block_id: UUID,
        clearing_actor_id: UUID,
        actor_may_decide: bool,
        note: str | None = None,
    ) -> LatenessBlock:
        """OPS-09 — support or operations clears the record, never the driver.

        ``actor_may_decide`` is passed in rather than inferred so the rule holds in the
        domain and not only in whichever route happened to call it.
        """
        if not actor_may_decide:
            raise OnlySupportMayClearABlock()

        self._uow.begin()
        try:
            block = self._uow.blocks.get(block_id)
            if block is None:
                raise BlockNotFound(str(block_id))
            if not block.is_active:
                raise BlockAlreadyCleared(str(block_id))
            block.cleared_at = _now()
            block.cleared_by_actor_id = clearing_actor_id
            block.clearing_note = (note or "").strip() or None
            block.version += 1
            self._uow.blocks.save(block)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return block

    def active_block(self, *, driver_id: UUID) -> LatenessBlock | None:
        self._uow.begin()
        try:
            block = self._uow.blocks.find_active(driver_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return block

    def list_active_blocks(self) -> tuple[LatenessBlock, ...]:
        self._uow.begin()
        try:
            found = self._uow.blocks.list_active()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def attendance_for(self, *, driver_id: UUID) -> tuple[AttendanceRecord, ...]:
        self._uow.begin()
        try:
            found = self._uow.attendance.list_for_driver(driver_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _require_driver(self, driver_id: UUID) -> DriverProfile:
        driver = self._uow.drivers.get(driver_id)
        if driver is None:
            raise DriverNotFound(str(driver_id))
        return driver


def _lateness_minutes(scheduled: time, actual: datetime) -> int:
    """Whole minutes late, floored at zero. An early start is not negative lateness."""
    scheduled_at = datetime.combine(actual.date(), scheduled, tzinfo=actual.tzinfo)
    if actual <= scheduled_at:
        return 0
    return int((actual - scheduled_at).total_seconds() // 60)
