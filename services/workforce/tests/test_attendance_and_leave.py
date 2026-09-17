"""Shifts, lateness, blocks, leave and assignment eligibility (DRV-A01…A04, OPS-09)."""

from __future__ import annotations

from datetime import date, time

import pytest
from workforce_fixtures import (
    APPLICANT,
    MONDAY,
    OPERATOR,
    SUPPORT,
    TUESDAY,
    at,
    attendance_service,
    build_store,
    eligibility_service,
    full_day_leave,
    hourly_leave,
    leave_service,
    morning_shift,
    onboarding_service,
    verified_driver,
)

from workforce.application.attendance_service import LatenessPolicy
from workforce.domain.errors import (
    BlockAlreadyCleared,
    LeaveDecisionNoteRequired,
    LeaveTransitionNotAllowed,
    NoShiftScheduled,
    OnlySupportMayClearABlock,
    OnlySupportMayDecideLeave,
    OverlappingLeaveRequest,
    ShiftAlreadyStarted,
    ShiftSelectionRequired,
)
from workforce.domain.value_objects import (
    AttendanceStatus,
    DriverStatus,
    LeaveKind,
    LeaveReason,
    LeaveStatus,
    LeaveWindow,
    ShiftSlot,
    ShiftWindow,
    Weekday,
)

# ------------------------------------------------------------------ DRV-A01


def test_starting_a_shift_records_attendance() -> None:
    uow = build_store()
    driver = verified_driver(uow)

    result = attendance_service(uow).start_shift(
        driver_id=driver.driver_id, moment=at(9, 0)
    )

    assert result.attendance.status is AttendanceStatus.STARTED
    assert result.attendance.scheduled_start == time(9, 0)
    assert result.attendance.lateness_minutes == 0


def test_an_early_start_is_not_negative_lateness() -> None:
    uow = build_store()
    driver = verified_driver(uow)

    result = attendance_service(uow).start_shift(
        driver_id=driver.driver_id, moment=at(8, 30)
    )

    assert result.attendance.lateness_minutes == 0
    assert result.attendance.was_late is False


def test_a_shift_cannot_be_started_twice() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    service.start_shift(driver_id=driver.driver_id, moment=at(9, 0))

    with pytest.raises(ShiftAlreadyStarted):
        service.start_shift(driver_id=driver.driver_id, moment=at(9, 5))


def test_a_shift_cannot_be_started_on_a_day_with_no_pattern() -> None:
    """The fixture pattern covers weekdays; 2026-09-19 is a Saturday."""
    uow = build_store()
    driver = verified_driver(uow)

    with pytest.raises(NoShiftScheduled):
        attendance_service(uow).start_shift(
            driver_id=driver.driver_id, moment=at(9, 0, day=date(2026, 9, 19))
        )


def test_assigned_work_unlocks_only_once_the_shift_starts() -> None:
    """Driver App v8 ``shift``: "Your assigned pickups unlock the moment you start"."""
    uow = build_store()
    driver = verified_driver(uow)

    before = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )
    attendance_service(uow).start_shift(driver_id=driver.driver_id, moment=at(9, 0))
    after = eligibility_service(uow).for_principal(principal_id=APPLICANT, day=MONDAY)

    assert before.eligible is False
    assert "SHIFT_NOT_STARTED" in before.reasons
    assert after.eligible is True
    assert after.shift_started is True


# ------------------------------------------------------------------ DRV-A02


def test_a_late_start_opens_a_block_with_the_delay_on_record() -> None:
    """The app shows shift start, when the driver opened it, and the delay."""
    uow = build_store()
    driver = verified_driver(uow)

    result = attendance_service(uow).start_shift(
        driver_id=driver.driver_id, moment=at(9, 52)
    )

    assert result.attendance.lateness_minutes == 52
    assert result.block is not None
    assert result.block.delay_minutes == 52
    assert result.block.scheduled_start == time(9, 0)
    assert result.block.is_active is True


def test_a_block_pauses_assignment_without_closing_the_account() -> None:
    """"Assignment is paused until support clears the record." """
    uow = build_store()
    driver = verified_driver(uow)
    attendance_service(uow).start_shift(driver_id=driver.driver_id, moment=at(9, 52))

    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )

    assert eligibility.eligible is False
    assert "LATENESS_BLOCK" in eligibility.reasons
    assert onboarding_service(uow).find_driver(
        principal_id=APPLICANT
    ).status is DriverStatus.ACTIVE


def test_the_lateness_threshold_is_configuration_not_a_guess() -> None:
    """v6.3 fixes no threshold, so a tolerant policy is a setting, not a code change."""
    uow = build_store()
    driver = verified_driver(uow)

    result = attendance_service(
        uow, policy=LatenessPolicy(grace_minutes=15, block_after_minutes=60)
    ).start_shift(driver_id=driver.driver_id, moment=at(9, 10))

    assert result.attendance.lateness_minutes == 10
    assert result.block is None


def test_a_driver_can_ask_support_to_look_but_not_clear_it_themselves() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    started = service.start_shift(driver_id=driver.driver_id, moment=at(9, 52))

    requested = service.request_unblock(driver_id=driver.driver_id)
    with pytest.raises(OnlySupportMayClearABlock):
        service.clear_block(
            block_id=started.block.block_id,
            clearing_actor_id=APPLICANT,
            actor_may_decide=False,
        )

    assert requested.unblock_requested is True
    assert service.active_block(driver_id=driver.driver_id) is not None


def test_support_clears_the_block_and_assignment_resumes() -> None:
    """OPS-09."""
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    started = service.start_shift(driver_id=driver.driver_id, moment=at(9, 52))

    cleared = service.clear_block(
        block_id=started.block.block_id,
        clearing_actor_id=SUPPORT,
        actor_may_decide=True,
        note="traffic closure on the bridge",
    )
    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )

    assert cleared.is_active is False
    assert cleared.cleared_by_actor_id == SUPPORT
    assert eligibility.eligible is True


def test_a_cleared_block_cannot_be_cleared_twice() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    started = service.start_shift(driver_id=driver.driver_id, moment=at(9, 52))
    service.clear_block(
        block_id=started.block.block_id,
        clearing_actor_id=SUPPORT,
        actor_may_decide=True,
    )

    with pytest.raises(BlockAlreadyCleared):
        service.clear_block(
            block_id=started.block.block_id,
            clearing_actor_id=SUPPORT,
            actor_may_decide=True,
        )


# ------------------------------------------------------------------ DRV-A03


def test_a_pending_leave_request_waives_the_lateness_penalty() -> None:
    """"No lateness penalty applies while your request is pending." """
    uow = build_store()
    driver = verified_driver(uow)
    leave_service(uow).request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.VEHICLE_PROBLEM,
        window=full_day_leave(MONDAY),
    )

    result = attendance_service(uow).start_shift(
        driver_id=driver.driver_id, moment=at(9, 52)
    )

    assert result.penalty_waived_by_leave is True
    assert result.block is None
    assert result.attendance.lateness_minutes == 52


def test_a_declined_leave_request_no_longer_protects() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.PERSONAL,
        window=full_day_leave(MONDAY),
    )
    service.decline(
        leave_id=request.leave_id,
        decider_principal_id=SUPPORT,
        actor_may_decide=True,
        note="no cover available",
    )

    result = attendance_service(uow).start_shift(
        driver_id=driver.driver_id, moment=at(9, 52)
    )

    assert result.penalty_waived_by_leave is False
    assert result.block is not None


def test_approved_leave_makes_a_driver_unassignable_that_day() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.SICK,
        window=full_day_leave(MONDAY),
    )
    service.approve(
        leave_id=request.leave_id,
        decider_principal_id=SUPPORT,
        actor_may_decide=True,
    )

    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )

    assert "ON_APPROVED_LEAVE" in eligibility.reasons


def test_approved_leave_does_not_affect_another_day() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.SICK,
        window=full_day_leave(MONDAY),
    )
    service.approve(
        leave_id=request.leave_id,
        decider_principal_id=SUPPORT,
        actor_may_decide=True,
    )

    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=TUESDAY
    )

    assert "ON_APPROVED_LEAVE" not in eligibility.reasons


def test_a_driver_cannot_approve_their_own_leave() -> None:
    """OPS-09."""
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.PERSONAL,
        window=full_day_leave(MONDAY),
    )

    with pytest.raises(OnlySupportMayDecideLeave):
        service.approve(
            leave_id=request.leave_id,
            decider_principal_id=APPLICANT,
            actor_may_decide=False,
        )


def test_a_declined_request_must_say_why() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.PERSONAL,
        window=full_day_leave(MONDAY),
    )

    with pytest.raises(LeaveDecisionNoteRequired):
        service.decline(
            leave_id=request.leave_id,
            decider_principal_id=SUPPORT,
            actor_may_decide=True,
            note="   ",
        )


def test_a_second_pending_request_for_the_same_day_is_refused() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    first = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.SICK,
        window=full_day_leave(MONDAY),
    )

    with pytest.raises(OverlappingLeaveRequest) as caught:
        service.request_leave(
            driver_id=driver.driver_id,
            reason=LeaveReason.PERSONAL,
            window=full_day_leave(MONDAY),
        )

    assert caught.value.reference == first.reference


def test_a_decided_request_cannot_be_decided_again() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = leave_service(uow)
    request = service.request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.SICK,
        window=full_day_leave(MONDAY),
    )
    service.approve(
        leave_id=request.leave_id,
        decider_principal_id=SUPPORT,
        actor_may_decide=True,
    )

    with pytest.raises(LeaveTransitionNotAllowed):
        service.decline(
            leave_id=request.leave_id,
            decider_principal_id=SUPPORT,
            actor_may_decide=True,
            note="changed our mind",
        )


def test_a_leave_request_carries_a_quotable_ticket_reference() -> None:
    uow = build_store()
    driver = verified_driver(uow)

    request = leave_service(uow).request_leave(
        driver_id=driver.driver_id,
        reason=LeaveReason.SICK,
        window=full_day_leave(MONDAY),
    )

    assert request.reference.startswith("LV-")
    assert request.status is LeaveStatus.PENDING


# ------------------------------------------------------------------ leave shape


def test_hourly_leave_must_state_its_hours() -> None:
    with pytest.raises(ValueError, match="must state the hours"):
        LeaveWindow(kind=LeaveKind.HOURLY, start_date=MONDAY, end_date=MONDAY)


def test_hourly_leave_sits_inside_a_single_day() -> None:
    with pytest.raises(ValueError, match="single day"):
        LeaveWindow(
            kind=LeaveKind.HOURLY,
            start_date=MONDAY,
            end_date=TUESDAY,
            starts_at=time(9),
            ends_at=time(13),
        )


def test_full_day_leave_carries_no_hours() -> None:
    with pytest.raises(ValueError, match="does not carry hours"):
        LeaveWindow(
            kind=LeaveKind.FULL_DAY,
            start_date=MONDAY,
            end_date=MONDAY,
            starts_at=time(9),
        )


def test_leave_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValueError, match="cannot end before"):
        LeaveWindow(kind=LeaveKind.FULL_DAY, start_date=TUESDAY, end_date=MONDAY)


def test_hourly_leave_covers_only_its_own_hours() -> None:
    window = hourly_leave(MONDAY)

    assert window.covers(MONDAY, time(10)) is True
    assert window.covers(MONDAY, time(14)) is False
    assert window.covers(TUESDAY, time(10)) is False


def test_the_four_leave_reasons_match_the_app() -> None:
    """Driver App v8 ``leave``: Sick, Family emergency, Vehicle problem, Personal."""
    assert {reason.value for reason in LeaveReason} == {
        "SICK",
        "FAMILY_EMERGENCY",
        "VEHICLE_PROBLEM",
        "PERSONAL",
    }


# ------------------------------------------------------------------ DRV-A04


def test_a_shift_pattern_change_applies_from_tomorrow() -> None:
    """Driver App v8 ``hoursEdit``: "Changes apply from tomorrow"."""
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)

    updated = service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(
            ShiftWindow(
                weekday=Weekday.TUESDAY,
                slot=ShiftSlot.EVENING,
                starts_at=time(16),
                ends_at=time(20),
            ),
        ),
        today=MONDAY,
    )

    assert updated.effective_from == TUESDAY


def test_todays_shift_is_unaffected_by_a_change_made_today() -> None:
    """Otherwise a change would retroactively make this morning's start late."""
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(
            ShiftWindow(
                weekday=Weekday.MONDAY,
                slot=ShiftSlot.EVENING,
                starts_at=time(16),
                ends_at=time(20),
            ),
        ),
        today=MONDAY,
    )

    result = service.start_shift(driver_id=driver.driver_id, moment=at(9, 0))

    assert result.attendance.scheduled_start == time(9, 0)
    assert result.attendance.lateness_minutes == 0


def test_a_pattern_must_name_at_least_one_shift() -> None:
    uow = build_store()
    driver = verified_driver(uow)

    with pytest.raises(ShiftSelectionRequired):
        attendance_service(uow).set_shift_pattern(
            driver_id=driver.driver_id, windows=(), today=MONDAY
        )


def test_a_driver_can_work_several_shifts_on_one_day() -> None:
    """"choose as many as you like" (Driver App v8 ``regHours``)."""
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(
            morning_shift(Weekday.TUESDAY),
            ShiftWindow(
                weekday=Weekday.TUESDAY,
                slot=ShiftSlot.EVENING,
                starts_at=time(16),
                ends_at=time(20),
            ),
        ),
        today=MONDAY,
    )

    pattern = service.current_pattern(driver_id=driver.driver_id, day=TUESDAY)
    assert len(pattern.windows_for(TUESDAY)) == 2


def test_the_earliest_window_sets_the_scheduled_start() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(
            ShiftWindow(
                weekday=Weekday.TUESDAY,
                slot=ShiftSlot.EVENING,
                starts_at=time(16),
                ends_at=time(20),
            ),
            morning_shift(Weekday.TUESDAY),
        ),
        today=MONDAY,
    )

    result = service.start_shift(driver_id=driver.driver_id, moment=at(9, 0, day=TUESDAY))

    assert result.attendance.scheduled_start == time(9, 0)


def test_a_shift_window_must_end_after_it_starts() -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        ShiftWindow(
            weekday=Weekday.MONDAY,
            slot=ShiftSlot.MORNING,
            starts_at=time(13),
            ends_at=time(9),
        )


# ------------------------------------------------------------------ eligibility


def test_a_suspended_driver_is_not_assignable() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    onboarding_service(uow).set_driver_status(
        driver_id=driver.driver_id, status=DriverStatus.SUSPENDED
    )

    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )

    assert eligibility.eligible is False
    assert "SUSPENDED" in eligibility.reasons


def test_eligibility_names_every_reason_at_once() -> None:
    """A caller can tell the driver everything to fix, not just the first problem."""
    uow = build_store()
    driver = verified_driver(uow)
    onboarding_service(uow).set_driver_status(
        driver_id=driver.driver_id, status=DriverStatus.SUSPENDED
    )

    eligibility = eligibility_service(uow).for_principal(
        principal_id=APPLICANT, day=date(2026, 9, 19)
    )

    assert set(eligibility.reasons) >= {"SUSPENDED", "NO_SHIFT_TODAY", "SHIFT_NOT_STARTED"}


def test_planning_before_the_shift_starts_can_be_allowed_explicitly() -> None:
    uow = build_store()
    verified_driver(uow)

    eligibility = eligibility_service(uow, require_started_shift=False).for_principal(
        principal_id=APPLICANT, day=MONDAY
    )

    assert eligibility.eligible is True
    assert eligibility.shift_started is False


def test_an_unknown_principal_is_simply_not_a_driver() -> None:
    uow = build_store()

    eligibility = eligibility_service(uow).for_principal(principal_id=OPERATOR)

    assert eligibility.driver_id is None
    assert eligibility.reasons == ("NO_DRIVER_PROFILE",)


def test_the_outgoing_pattern_still_covers_the_day_it_was_replaced_on() -> None:
    """The bug this guards: superseding a pattern "now" erases today's shift entirely.

    A driver who changes their hours at lunchtime must still have had a shift this
    morning, otherwise starting it becomes impossible and their attendance vanishes.
    """
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)

    service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(morning_shift(Weekday.TUESDAY),),
        today=MONDAY,
    )

    today = service.current_pattern(driver_id=driver.driver_id, day=MONDAY)
    tomorrow = service.current_pattern(driver_id=driver.driver_id, day=TUESDAY)
    assert today is not None
    assert today.effective_to == MONDAY
    assert tomorrow is not None
    assert tomorrow.effective_from == TUESDAY


def test_the_replaced_pattern_stops_covering_the_following_day() -> None:
    uow = build_store()
    driver = verified_driver(uow)
    service = attendance_service(uow)
    service.set_shift_pattern(
        driver_id=driver.driver_id,
        windows=(
            ShiftWindow(
                weekday=Weekday.TUESDAY,
                slot=ShiftSlot.EVENING,
                starts_at=time(16),
                ends_at=time(20),
            ),
        ),
        today=MONDAY,
    )

    result = service.start_shift(driver_id=driver.driver_id, moment=at(16, 0, day=TUESDAY))

    assert result.attendance.scheduled_start == time(16, 0)
