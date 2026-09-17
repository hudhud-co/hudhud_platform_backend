"""Driver assignment response and forward-only pickup task progress."""

from __future__ import annotations

from uuid import uuid4

import pytest
from driver_fixtures import (
    BASE_TIME,
    DRIVER_ID,
    build_store,
    driver_actor,
    lifecycle_service,
    minutes,
    register_task,
    start_session,
)

from pickup.application.task_lifecycle_service import (
    CaptureProofCommand,
    DeclineAssignmentCommand,
    FailTaskCommand,
    ReportExceptionCommand,
    ScanTaskCommand,
    TaskCommand,
    current_assignment_revision,
)
from pickup.domain.errors import (
    ActingDriverMismatch,
    AssignmentAlreadyResolved,
    AssignmentNotAcknowledged,
    DriverNotAvailableForAssignment,
    ExceptionEvidenceInsufficient,
    InvalidTaskTransition,
    PickupTaskNotFound,
    ScannedIdentifierMismatch,
)
from pickup.domain.value_objects import (
    AssignmentState,
    PickupExceptionReason,
    PickupTaskStatus,
)


def _command(task_id, *, at_minutes: int = 1, driver: str = DRIVER_ID) -> TaskCommand:
    return TaskCommand(
        pickup_task_id=task_id,
        acting_driver_user_id=driver,
        occurred_at=BASE_TIME + minutes(at_minutes),
    )


def _acknowledged(store):
    task = register_task(store)
    lifecycle_service(store).acknowledge(
        _command(task.pickup_task_id), actor=driver_actor()
    )
    return task


# --------------------------------------------------------------- assignment


def test_acknowledge_requires_an_online_work_session() -> None:
    store = build_store()
    task = register_task(store)
    with pytest.raises(DriverNotAvailableForAssignment):
        lifecycle_service(store).acknowledge(
            _command(task.pickup_task_id), actor=driver_actor()
        )


def test_acknowledge_marks_the_assignment_and_changes_the_revision() -> None:
    store = build_store()
    start_session(store)
    task = register_task(store)
    before = current_assignment_revision(task)
    result = lifecycle_service(store).acknowledge(
        _command(task.pickup_task_id), actor=driver_actor()
    )
    assert result.task.assignment_state is AssignmentState.ACKNOWLEDGED
    assert result.assignment_revision != before


def test_forward_progress_does_not_change_the_assignment_revision() -> None:
    """A driver captures a whole offline batch under one downloaded revision."""
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    acknowledged_revision = current_assignment_revision(
        store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    )
    arrived = service.arrive(
        _command(task.pickup_task_id, at_minutes=5), actor=driver_actor()
    )
    assert arrived.assignment_revision == acknowledged_revision


def test_repeat_acknowledge_is_a_replay() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    again = lifecycle_service(store).acknowledge(
        _command(task.pickup_task_id, at_minutes=2), actor=driver_actor()
    )
    assert again.replayed is True


def test_decline_after_acknowledge_is_rejected() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(AssignmentAlreadyResolved):
        lifecycle_service(store).decline(
            DeclineAssignmentCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(3),
                reason="VEHICLE_ISSUE",
            ),
            actor=driver_actor(),
        )


def test_decline_does_not_require_an_online_session() -> None:
    store = build_store()
    task = register_task(store)
    result = lifecycle_service(store).decline(
        DeclineAssignmentCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(3),
            reason="SAFETY_CONCERN",
        ),
        actor=driver_actor(),
    )
    assert result.task.assignment_state is AssignmentState.DECLINED
    assert result.task.declined_reason is not None


def test_only_the_assigned_driver_may_act() -> None:
    store = build_store()
    start_session(store, driver_user_id="driver-99")
    task = register_task(store)
    with pytest.raises(ActingDriverMismatch):
        lifecycle_service(store).acknowledge(
            _command(task.pickup_task_id, driver="driver-99"),
            actor=driver_actor("driver-99"),
        )


def test_unknown_task_is_not_found() -> None:
    store = build_store()
    with pytest.raises(PickupTaskNotFound):
        lifecycle_service(store).acknowledge(_command(uuid4()), actor=driver_actor())


# ------------------------------------------------------------------ progress


def test_progress_requires_an_acknowledged_assignment() -> None:
    store = build_store()
    start_session(store)
    task = register_task(store)
    with pytest.raises(AssignmentNotAcknowledged):
        lifecycle_service(store).arrive(
            _command(task.pickup_task_id), actor=driver_actor()
        )


def test_forward_only_progress_through_arrive_scan_proof() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)

    arrived = service.arrive(
        _command(task.pickup_task_id, at_minutes=5), actor=driver_actor()
    )
    assert arrived.task.status is PickupTaskStatus.ARRIVED

    scanned = service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(6),
            scanned_identifier="WB-1001",
        ),
        actor=driver_actor(),
    )
    assert scanned.task.status is PickupTaskStatus.SCANNED

    proof = service.capture_proof(
        CaptureProofCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(7),
            package_condition_status="GOOD",
        ),
        actor=driver_actor(),
    )
    assert proof.task.status is PickupTaskStatus.PROOF_CAPTURED
    assert proof.task.has_pickup_condition_proof is True


def test_skipping_a_step_is_rejected() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(InvalidTaskTransition):
        lifecycle_service(store).capture_proof(
            CaptureProofCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
                package_condition_status="GOOD",
            ),
            actor=driver_actor(),
        )


def test_repeating_a_reached_step_is_an_idempotent_replay() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    service.arrive(_command(task.pickup_task_id, at_minutes=5), actor=driver_actor())
    again = service.arrive(
        _command(task.pickup_task_id, at_minutes=6), actor=driver_actor()
    )
    assert again.replayed is True
    assert again.task.status is PickupTaskStatus.ARRIVED


def test_rescanning_a_different_identifier_is_rejected() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    service.arrive(_command(task.pickup_task_id, at_minutes=5), actor=driver_actor())
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(6),
            scanned_identifier="WB-1001",
        ),
        actor=driver_actor(),
    )
    with pytest.raises(InvalidTaskTransition):
        service.scan(
            ScanTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(7),
                scanned_identifier="WB-9999",
            ),
            actor=driver_actor(),
        )


def test_scan_requires_a_non_empty_identifier() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(ScannedIdentifierMismatch):
        lifecycle_service(store).scan(
            ScanTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
                scanned_identifier="   ",
            ),
            actor=driver_actor(),
        )


def test_damaged_condition_requires_notes_or_evidence() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    service.arrive(_command(task.pickup_task_id, at_minutes=5), actor=driver_actor())
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(6),
            scanned_identifier="WB-1001",
        ),
        actor=driver_actor(),
    )
    with pytest.raises(ExceptionEvidenceInsufficient):
        service.capture_proof(
            CaptureProofCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(7),
                package_condition_status="MAJOR_DAMAGE",
            ),
            actor=driver_actor(),
        )


# ----------------------------------------------------------------- exceptions


def test_cannot_contact_sender_requires_a_recorded_contact_attempt() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(ExceptionEvidenceInsufficient):
        lifecycle_service(store).report_exception(
            ReportExceptionCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
                reason="CANNOT_CONTACT_SENDER",
                notes="tried twice",
            ),
            actor=driver_actor(),
        )


def test_cannot_contact_sender_is_accepted_with_attempts() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    result = lifecycle_service(store).report_exception(
        ReportExceptionCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
            reason="CANNOT_CONTACT_SENDER",
            contact_attempted=True,
            contact_attempt_count=2,
        ),
        actor=driver_actor(),
    )
    assert result.task.status is PickupTaskStatus.EXCEPTION_REPORTED
    assert result.task.exception_reason is PickupExceptionReason.CANNOT_CONTACT_SENDER


def test_damaged_package_exception_requires_notes_or_condition_proof() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(ExceptionEvidenceInsufficient):
        lifecycle_service(store).report_exception(
            ReportExceptionCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
                reason="DAMAGED_PACKAGE",
            ),
            actor=driver_actor(),
        )


def test_wrong_address_exception_needs_no_extra_evidence() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    result = lifecycle_service(store).report_exception(
        ReportExceptionCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
            reason="WRONG_ADDRESS",
        ),
        actor=driver_actor(),
    )
    assert result.task.status is PickupTaskStatus.EXCEPTION_REPORTED


def test_fail_is_terminal_for_the_attempt_but_stays_recoverable() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    result = lifecycle_service(store).fail(
        FailTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(9),
            reason="SENDER_REFUSED_HANDOVER",
        ),
        actor=driver_actor(),
    )
    assert result.task.status is PickupTaskStatus.FAILED
    assert result.task.is_terminal is False


def test_repeat_fail_is_a_replay() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    command = FailTaskCommand(
        pickup_task_id=task.pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        occurred_at=BASE_TIME + minutes(9),
        reason="ACCESS_BLOCKED",
    )
    service.fail(command, actor=driver_actor())
    assert service.fail(command, actor=driver_actor()).replayed is True


def test_history_is_appended_for_each_transition_with_the_acting_driver() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    lifecycle_service(store).arrive(
        _command(task.pickup_task_id, at_minutes=5), actor=driver_actor()
    )
    actions = [
        entry.action
        for entry in store.task_history.list_entries_for_task(task.pickup_task_id)
    ]
    assert actions == ["assignment_acknowledged", "task_arrived"]
    assert all(
        entry.actor_id == DRIVER_ID
        for entry in store.task_history.list_entries_for_task(task.pickup_task_id)
    )


def test_failed_command_leaves_no_partial_state() -> None:
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    with pytest.raises(InvalidTaskTransition):
        lifecycle_service(store).capture_proof(
            CaptureProofCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
                package_condition_status="GOOD",
            ),
            actor=driver_actor(),
        )
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.PENDING
    assert reloaded.has_pickup_condition_proof is False


def test_an_exception_report_forces_the_driver_to_re_progress_before_acceptance() -> None:
    """Reporting an exception takes the task off the forward path on purpose."""
    store = build_store()
    start_session(store)
    task = _acknowledged(store)
    service = lifecycle_service(store)
    service.arrive(_command(task.pickup_task_id, at_minutes=5), actor=driver_actor())
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(6),
            scanned_identifier="WB-1001",
        ),
        actor=driver_actor(),
    )
    reported = service.report_exception(
        ReportExceptionCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(7),
            reason="PACKAGE_NOT_READY",
        ),
        actor=driver_actor(),
    )
    assert reported.task.status is PickupTaskStatus.EXCEPTION_REPORTED
    # Jumping straight back to proof capture is refused; the driver re-walks the path.
    with pytest.raises(InvalidTaskTransition):
        service.capture_proof(
            CaptureProofCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(8),
                package_condition_status="GOOD",
            ),
            actor=driver_actor(),
        )
    resumed = service.arrive(
        _command(task.pickup_task_id, at_minutes=9), actor=driver_actor()
    )
    assert resumed.task.status is PickupTaskStatus.ARRIVED
