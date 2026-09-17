"""Offline work authorization, append-only replay, and reconciliation cases."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from driver_fixtures import (
    BASE_TIME,
    DRIVER_ID,
    build_store,
    driver_actor,
    lifecycle_service,
    minutes,
    offline_service,
    operations_actor,
    register_task,
    start_session,
    work_session_service,
)

from pickup.application.driver_session_service import EndWorkSessionCommand
from pickup.application.offline_sync_service import (
    IssueOfflineAuthorizationCommand,
    OfflineEventSubmission,
    OfflinePolicy,
    ResolveReconciliationCommand,
    RevokeOfflineAuthorizationCommand,
    SyncOfflineWorkCommand,
)
from pickup.application.task_lifecycle_service import (
    TaskCommand,
    current_assignment_revision,
)
from pickup.domain.errors import (
    ActingDriverMismatch,
    DriverNotAvailableForAssignment,
    DriverWorkSessionBlocked,
    OfflineAuthorizationInvalid,
    OfflineAuthorizationNotFound,
    OfflineAuthorizationRevoked,
    OfflineBatchTooLarge,
    OfflineSyncDeadlinePassed,
    ReconciliationCaseAlreadyResolved,
    ReconciliationCaseNotFound,
)
from pickup.domain.offline import (
    OfflineEventStatus,
    OfflineOperation,
    OfflineOutcomeCode,
    ReconciliationCaseStatus,
    ReconciliationResolution,
)
from pickup.domain.security import offline_event_fingerprint
from pickup.domain.value_objects import AssignmentState, PickupTaskStatus
from pickup.domain.workforce import WorkSessionBlocker

DEVICE = "device-abcdef123456"


def _acknowledged_task(store):
    start_session(store)
    task = register_task(store)
    lifecycle_service(store).acknowledge(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(1),
        ),
        actor=driver_actor(),
    )
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    return reloaded


def _issue(store, task, *, service=None, policy: OfflinePolicy | None = None):
    service = service or offline_service(store, policy=policy)
    return service.issue_authorization(
        IssueOfflineAuthorizationCommand(
            driver_user_id=DRIVER_ID,
            device_id=DEVICE,
            resource_id=task.pickup_task_id,
            occurred_at=BASE_TIME + minutes(2),
        ),
        actor=driver_actor(),
    )


def _submission(
    *,
    task,
    sequence: int,
    operation: OfflineOperation,
    revision: str,
    at_minutes: int = 10,
    payload: dict | None = None,
    operation_id=None,
    fingerprint: str | None = None,
) -> OfflineEventSubmission:
    operation_id = operation_id or uuid4()
    captured_at = BASE_TIME + minutes(at_minutes)
    payload = payload or {}
    computed = offline_event_fingerprint(
        operation_id=operation_id,
        sequence=sequence,
        operation=operation.value,
        resource_type="PICKUP_TASK",
        resource_id=task.pickup_task_id,
        assignment_revision_value=revision,
        captured_at=captured_at,
        payload=payload,
    )
    return OfflineEventSubmission(
        operation_id=operation_id,
        sequence=sequence,
        operation=operation,
        resource_id=task.pickup_task_id,
        assignment_revision=revision,
        captured_at=captured_at,
        payload_fingerprint=fingerprint or computed,
        payload=payload,
    )


def _sync(service, *, token, events, stream_id=None, at_minutes: int = 20):
    return service.sync(
        SyncOfflineWorkCommand(
            driver_user_id=DRIVER_ID,
            device_id=DEVICE,
            stream_id=stream_id or uuid4(),
            authorization_token=token,
            events=tuple(events),
            occurred_at=BASE_TIME + minutes(at_minutes),
        ),
        actor=driver_actor(),
    )


# ------------------------------------------------------------- authorization


def test_authorization_binds_driver_device_resource_and_revision() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    issued = _issue(store, task)
    assert issued.authorization.driver_user_id == DRIVER_ID
    assert issued.authorization.resource_id == task.pickup_task_id
    assert issued.authorization.assignment_revision == current_assignment_revision(task)
    assert issued.authorization.device_id_hash != DEVICE
    assert issued.token


def test_offline_authority_never_includes_custody_acceptance() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    issued = _issue(store, task)
    assert OfflineOperation.ACCEPT_CUSTODY not in issued.authorization.permitted_operations


def test_downloading_work_requires_an_online_session() -> None:
    store = build_store()
    task = register_task(store, assignment_state=AssignmentState.ACKNOWLEDGED)
    with pytest.raises(DriverNotAvailableForAssignment):
        _issue(store, task)


def test_only_the_assigned_driver_may_download_the_work() -> None:
    store = build_store()
    start_session(store)
    task = register_task(
        store, driver_user_id="driver-99", assignment_state=AssignmentState.ACKNOWLEDGED
    )
    with pytest.raises(ActingDriverMismatch):
        _issue(store, task)


def test_a_revoked_authorization_cannot_sync() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    service.revoke_authorization(
        RevokeOfflineAuthorizationCommand(
            authorization_id=issued.authorization.authorization_id,
            driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
            reason="device lost",
        ),
        actor=driver_actor(),
    )
    with pytest.raises(OfflineAuthorizationRevoked):
        _sync(service, token=issued.token, events=[])


def test_a_tampered_token_is_refused_before_any_claim_is_trusted() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    body, _, _signature = issued.token.partition(".")
    with pytest.raises(OfflineAuthorizationInvalid):
        _sync(service, token=f"{body}.AAAA", events=[])


def test_a_token_for_another_device_is_refused() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    with pytest.raises(OfflineAuthorizationInvalid):
        service.sync(
            SyncOfflineWorkCommand(
                driver_user_id=DRIVER_ID,
                device_id="device-someone-elses",
                stream_id=uuid4(),
                authorization_token=issued.token,
                events=(),
                occurred_at=BASE_TIME + minutes(20),
            ),
            actor=driver_actor(),
        )


def test_syncing_after_the_deadline_is_refused() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(
        store, policy=OfflinePolicy(authorization_ttl_minutes=1, sync_grace_days=0)
    )
    issued = _issue(store, task, service=service)
    with pytest.raises(OfflineSyncDeadlinePassed):
        _sync(service, token=issued.token, events=[], at_minutes=60)


def test_an_oversized_batch_is_refused_whole() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store, policy=OfflinePolicy(max_sync_events=1))
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    with pytest.raises(OfflineBatchTooLarge):
        _sync(
            service,
            token=issued.token,
            events=[
                _submission(
                    task=task,
                    sequence=1,
                    operation=OfflineOperation.ARRIVE,
                    revision=revision,
                ),
                _submission(
                    task=task,
                    sequence=2,
                    operation=OfflineOperation.SCAN,
                    revision=revision,
                    payload={"scanned_identifier": "WB-1"},
                ),
            ],
        )


def test_an_unknown_authorization_is_not_found() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    other_store = build_store()
    other_task = _acknowledged_task(other_store)
    other_service = offline_service(other_store)
    _issue(other_store, other_task, service=other_service)
    with pytest.raises(OfflineAuthorizationNotFound):
        _sync(other_service, token=issued.token, events=[])


# --------------------------------------------------------------------- replay


def test_a_contiguous_batch_applies_through_the_owning_workflow() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                at_minutes=10,
            ),
        ],
    )
    assert result.counts["applied"] == 1
    assert result.last_contiguous_sequence == 1
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.ARRIVED


def test_a_sequence_gap_opens_a_case_without_applying() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=2,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
            )
        ],
    )
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.RECONCILIATION_REQUIRED
    assert outcome.outcome_code is OfflineOutcomeCode.SEQUENCE_GAP
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.PENDING
    assert len(service.list_cases()) == 1


def test_a_forged_fingerprint_is_rejected_and_preserved() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                fingerprint="0" * 64,
            )
        ],
    )
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.REJECTED
    assert outcome.outcome_code is OfflineOutcomeCode.PAYLOAD_FINGERPRINT_MISMATCH
    assert len(store.offline_events.list_for_driver(DRIVER_ID)) == 1


def test_a_stale_assignment_revision_opens_a_case() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision="stale-revision-value",
            )
        ],
    )
    outcome = result.outcomes[0]
    assert outcome.outcome_code is OfflineOutcomeCode.STALE_ASSIGNMENT_REVISION
    assert outcome.status is OfflineEventStatus.RECONCILIATION_REQUIRED


def test_a_capture_from_the_future_opens_a_case() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                at_minutes=600,
            )
        ],
        at_minutes=20,
    )
    assert result.outcomes[0].outcome_code is OfflineOutcomeCode.CAPTURE_TIME_IN_FUTURE


def test_a_capture_predating_the_authorization_opens_a_case() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                at_minutes=-60,
            )
        ],
    )
    assert (
        result.outcomes[0].outcome_code
        is OfflineOutcomeCode.CAPTURE_BEFORE_AUTHORIZATION
    )


def test_custody_acceptance_captured_offline_is_deferred_not_applied() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    submission = _submission(
        task=task,
        sequence=1,
        operation=OfflineOperation.ACCEPT_CUSTODY,
        revision=revision,
        payload={"scanned_identifier": "WB-1"},
    )
    result = _sync(service, token=issued.token, events=[submission])
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.REJECTED
    assert outcome.outcome_code is OfflineOutcomeCode.OPERATION_NOT_AUTHORIZED
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.is_accepted is False


def test_a_domain_rejection_preserves_the_capture_and_opens_a_case() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    # CAPTURE_PROOF before ARRIVE is a forward-only violation.
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.CAPTURE_PROOF,
                revision=revision,
                payload={"package_condition_status": "GOOD"},
            )
        ],
    )
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.RECONCILIATION_REQUIRED
    assert outcome.outcome_code is OfflineOutcomeCode.DOMAIN_REJECTED
    cases = service.list_cases()
    assert len(cases) == 1
    assert cases[0].status is ReconciliationCaseStatus.OPEN
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.PENDING


def test_one_failed_capture_does_not_discard_the_rest_of_the_batch() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.CAPTURE_PROOF,
                revision=revision,
                payload={"package_condition_status": "GOOD"},
                at_minutes=10,
            ),
            _submission(
                task=task,
                sequence=2,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                at_minutes=11,
            ),
        ],
    )
    assert result.counts["reconciliation_required"] == 1
    assert result.counts["applied"] == 1
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.ARRIVED


def test_resending_the_same_capture_returns_the_stored_outcome() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    stream_id = uuid4()
    submission = _submission(
        task=task, sequence=1, operation=OfflineOperation.ARRIVE, revision=revision
    )
    first = _sync(
        service, token=issued.token, events=[submission], stream_id=stream_id
    )
    second = _sync(
        service,
        token=issued.token,
        events=[submission],
        stream_id=stream_id,
        at_minutes=21,
    )
    assert second.outcomes[0].replayed is True
    assert second.outcomes[0].event_row_id == first.outcomes[0].event_row_id
    assert len(store.offline_events.list_for_driver(DRIVER_ID)) == 1


def test_reusing_an_operation_id_with_new_content_is_preserved_as_a_conflict() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    stream_id = uuid4()
    operation_id = uuid4()
    first = _submission(
        task=task,
        sequence=1,
        operation=OfflineOperation.ARRIVE,
        revision=revision,
        operation_id=operation_id,
    )
    _sync(service, token=issued.token, events=[first], stream_id=stream_id)
    forged = _submission(
        task=task,
        sequence=2,
        operation=OfflineOperation.FAIL,
        revision=revision,
        operation_id=operation_id,
        payload={"reason": "OTHER", "notes": "forged"},
    )
    result = _sync(
        service,
        token=issued.token,
        events=[forged],
        stream_id=stream_id,
        at_minutes=21,
    )
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.REJECTED
    assert outcome.outcome_code is OfflineOutcomeCode.OPERATION_ID_REUSE
    cases = service.list_cases()
    assert len(cases) == 1
    assert cases[0].submitted_event["conflicting_replays"]
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.ARRIVED


def test_reusing_a_stream_sequence_is_rejected_and_preserved() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    stream_id = uuid4()
    _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
            )
        ],
        stream_id=stream_id,
    )
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=reloaded,
                sequence=1,
                operation=OfflineOperation.SCAN,
                revision=current_assignment_revision(reloaded),
                payload={"scanned_identifier": "WB-1"},
            )
        ],
        stream_id=stream_id,
        at_minutes=21,
    )
    assert result.outcomes[0].outcome_code is OfflineOutcomeCode.SEQUENCE_REUSE


def test_a_resent_gap_applies_once_the_missing_sequence_arrives() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    stream_id = uuid4()
    gapped = _submission(
        task=task,
        sequence=2,
        operation=OfflineOperation.SCAN,
        revision=revision,
        payload={"scanned_identifier": "WB-1"},
        at_minutes=11,
    )
    first = _sync(
        service, token=issued.token, events=[gapped], stream_id=stream_id
    )
    assert first.outcomes[0].outcome_code is OfflineOutcomeCode.SEQUENCE_GAP

    # The driver's earlier ARRIVE arrives late; the stream restarts contiguously.
    store.begin()
    stream = store.offline_streams.get_stream(stream_id)
    assert stream is not None
    stream.last_contiguous_sequence = 1
    store.offline_streams.save_stream(stream)
    store.commit()
    lifecycle_service(store).arrive(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(10),
        ),
        actor=driver_actor(),
    )

    second = _sync(
        service,
        token=issued.token,
        events=[gapped],
        stream_id=stream_id,
        at_minutes=25,
    )
    outcome = second.outcomes[0]
    assert outcome.replayed is True
    assert outcome.status is OfflineEventStatus.APPLIED
    cases = service.list_cases()
    assert cases[0].status is ReconciliationCaseStatus.RESOLVED
    assert cases[0].resolution is ReconciliationResolution.ORDER_RESTORED_AND_APPLIED


# ------------------------------------------------------------ reconciliation


def test_operations_can_resolve_an_open_case_once() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.CAPTURE_PROOF,
                revision=revision,
                payload={"package_condition_status": "GOOD"},
            )
        ],
    )
    case = service.list_cases()[0]
    resolved = service.resolve_case(
        ResolveReconciliationCommand(
            case_id=case.case_id,
            resolution="SERVER_STATE_AUTHORITATIVE",
            occurred_at=BASE_TIME + minutes(30),
            notes="server order is authoritative",
        ),
        actor=operations_actor(),
    )
    assert resolved.status is ReconciliationCaseStatus.RESOLVED
    assert resolved.resolved_by_user_id == operations_actor().actor_id
    with pytest.raises(ReconciliationCaseAlreadyResolved):
        service.resolve_case(
            ResolveReconciliationCommand(
                case_id=case.case_id,
                resolution="DISMISSED_NO_ACTION",
                occurred_at=BASE_TIME + minutes(31),
            ),
            actor=operations_actor(),
        )


def test_resolving_an_unknown_case_is_not_found() -> None:
    store = build_store()
    with pytest.raises(ReconciliationCaseNotFound):
        offline_service(store).resolve_case(
            ResolveReconciliationCommand(
                case_id=uuid4(),
                resolution="DISMISSED_NO_ACTION",
                occurred_at=BASE_TIME + minutes(30),
            ),
            actor=operations_actor(),
        )


def test_unreconciled_offline_work_blocks_ending_the_session() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.CAPTURE_PROOF,
                revision=revision,
                payload={"package_condition_status": "GOOD"},
            )
        ],
    )
    session = store.work_sessions.get_open_session_for_driver(DRIVER_ID)
    assert session is not None
    with pytest.raises(DriverWorkSessionBlocked) as excinfo:
        work_session_service(store).end(
            EndWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=session.session_id,
                reason="SESSION_COMPLETE",
                occurred_at=BASE_TIME + minutes(40),
            ),
            actor=driver_actor(),
        )
    assert WorkSessionBlocker.UNSYNCED_OFFLINE_WORK.value in excinfo.value.blockers


def test_clock_skew_within_tolerance_is_accepted() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    captured = issued.authorization.issued_at - timedelta(minutes=2)
    operation_id = uuid4()
    fingerprint = offline_event_fingerprint(
        operation_id=operation_id,
        sequence=1,
        operation=OfflineOperation.ARRIVE.value,
        resource_type="PICKUP_TASK",
        resource_id=task.pickup_task_id,
        assignment_revision_value=revision,
        captured_at=captured,
        payload={},
    )
    result = _sync(
        service,
        token=issued.token,
        events=[
            OfflineEventSubmission(
                operation_id=operation_id,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                resource_id=task.pickup_task_id,
                assignment_revision=revision,
                captured_at=captured,
                payload_fingerprint=fingerprint,
            )
        ],
    )
    assert result.counts["applied"] == 1


def test_a_whole_offline_shift_replays_under_one_downloaded_revision() -> None:
    """The end-to-end property the assignment revision exists to protect."""
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
                at_minutes=10,
            ),
            _submission(
                task=task,
                sequence=2,
                operation=OfflineOperation.SCAN,
                revision=revision,
                payload={"scanned_identifier": "WB-1001"},
                at_minutes=11,
            ),
            _submission(
                task=task,
                sequence=3,
                operation=OfflineOperation.CAPTURE_PROOF,
                revision=revision,
                payload={"package_condition_status": "GOOD"},
                at_minutes=12,
            ),
        ],
    )
    assert result.counts["applied"] == 3
    assert result.counts["reconciliation_required"] == 0
    assert result.last_contiguous_sequence == 3
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.PROOF_CAPTURED
    assert reloaded.scanned_identifier == "WB-1001"


def test_reassigning_the_work_makes_every_offline_capture_stale() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision

    # Operations moves the work to another driver after the download.
    store.begin()
    reassigned = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reassigned is not None
    reassigned.assigned_driver_user_id = "driver-99"
    store.pickup_tasks.save_pickup_task(reassigned)
    store.commit()

    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
            )
        ],
    )
    outcome = result.outcomes[0]
    assert outcome.status is OfflineEventStatus.RECONCILIATION_REQUIRED
    assert outcome.outcome_code is OfflineOutcomeCode.ASSIGNMENT_NO_LONGER_OWNED
    assert service.list_cases()[0].custody_implication is False


def test_declining_the_assignment_makes_offline_captures_stale() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision

    store.begin()
    declined = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert declined is not None
    declined.assignment_state = AssignmentState.DECLINED
    store.pickup_tasks.save_pickup_task(declined)
    store.commit()

    result = _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.ARRIVE,
                revision=revision,
            )
        ],
    )
    assert (
        result.outcomes[0].outcome_code is OfflineOutcomeCode.STALE_ASSIGNMENT_REVISION
    )


def test_an_offline_fail_capture_is_flagged_as_a_custody_case() -> None:
    store = build_store()
    task = _acknowledged_task(store)
    service = offline_service(store)
    issued = _issue(store, task, service=service)
    revision = issued.authorization.assignment_revision
    # A FAIL captured against a stale revision must reach Operations as custody-relevant.
    store.begin()
    moved = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert moved is not None
    moved.attempt_number += 1
    store.pickup_tasks.save_pickup_task(moved)
    store.commit()
    _sync(
        service,
        token=issued.token,
        events=[
            _submission(
                task=task,
                sequence=1,
                operation=OfflineOperation.FAIL,
                revision=revision,
                payload={"reason": "ACCESS_BLOCKED"},
            )
        ],
    )
    case = service.list_cases()[0]
    assert case.reason_code is OfflineOutcomeCode.STALE_ASSIGNMENT_REVISION
    assert case.custody_implication is True
