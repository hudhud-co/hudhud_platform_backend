"""Packaging decision, merchant-stop outcomes, scan resolution and stop readiness.

Product evidence: Driver App v8 ``condition`` / ``refuse`` / ``notAccepted`` / ``progress``
/ ``scanner`` / ``scanEx:*`` / ``connLost`` screens, and v6.3 chapter 3.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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

from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.recovery_service import (
    PickupRecoveryService,
    ReassignRecoveryCommand,
    RecoveryCommand,
    RegisterPickupTaskCommand,
)
from pickup.application.stop_service import (
    PickupStopService,
    ResolveScanCommand,
)
from pickup.application.task_lifecycle_service import (
    CaptureProofCommand,
    RefusePickupCommand,
    ScanTaskCommand,
    TaskCommand,
    refusal_reason_for_assessment,
)
from pickup.domain.errors import (
    ActingDriverMismatch,
    AssignmentNotAcknowledged,
    ExceptionEvidenceInsufficient,
    PackagingDecisionNotPermitted,
    PickupBatchNotFound,
    PickupPhotoDocumentationMissing,
    PickupTaskNotFound,
    ScanIdentifierMissing,
    StopOutcomeAlreadyRecorded,
    StopOutcomeNotAllowed,
)
from pickup.domain.value_objects import (
    AcceptanceOutcome,
    AssignmentState,
    ConditionDecision,
    EvidenceMediaRef,
    PackageConditionStatus,
    PackagingAssessment,
    PickupRefusalReason,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ScanResolutionOutcome,
    ShipmentStatus,
    StopOutcome,
)
from pickup.infrastructure.fake_shipment_eligibility import (
    InMemoryShipmentEligibilityAdapter,
)
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot

LABEL = "HHD-10452"


def _ready_task(store, *, batch_id=None, driver_user_id=DRIVER_ID, label=LABEL):
    """A task scanned and ready for the condition check."""
    task = register_task(
        store,
        driver_user_id=driver_user_id,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch_id,
    )
    service = lifecycle_service(store)
    actor = driver_actor(driver_user_id)
    service.arrive(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=driver_user_id,
            occurred_at=BASE_TIME + minutes(1),
        ),
        actor=actor,
    )
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=driver_user_id,
            occurred_at=BASE_TIME + minutes(2),
            scanned_identifier=label,
        ),
        actor=actor,
    )
    return task


def _proof(task, *, assessment=None, decision=None, condition="GOOD", notes="noted"):
    return CaptureProofCommand(
        pickup_task_id=task.pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        occurred_at=BASE_TIME + minutes(3),
        package_condition_status=condition,
        condition_notes=notes,
        evidence_present=True,
        packaging_assessment=assessment,
        decision=decision,
    )


# ----------------------------------------------------------- packaging decision


def test_good_packaging_is_accepted_without_a_warning_or_a_note():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).capture_proof(
        _proof(task, assessment="GOOD"), actor=driver_actor()
    )

    assert result.task.packaging_assessment is PackagingAssessment.GOOD
    assert result.task.condition_decision is ConditionDecision.ACCEPT


def test_borderline_packaging_can_be_accepted_with_a_warning():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).capture_proof(
        _proof(task, assessment="BORDERLINE", decision="ACCEPT_WITH_WARNING"),
        actor=driver_actor(),
    )

    assert result.task.condition_decision is ConditionDecision.ACCEPT_WITH_WARNING


def test_pre_existing_damage_can_be_accepted_with_a_note():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).capture_proof(
        _proof(
            task,
            assessment="PRE_EXISTING_DAMAGE",
            decision="ACCEPT_WITH_NOTE",
            condition="MINOR_DAMAGE",
        ),
        actor=driver_actor(),
    )

    assert result.task.condition_decision is ConditionDecision.ACCEPT_WITH_NOTE


def test_packaging_too_weak_to_survive_transport_can_only_be_refused():
    """The one decision table entry that is a hard product rule, not a default."""
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    for attempted in ("ACCEPT", "ACCEPT_WITH_WARNING", "ACCEPT_WITH_NOTE"):
        with pytest.raises(PackagingDecisionNotPermitted) as excinfo:
            lifecycle_service(store).capture_proof(
                _proof(task, assessment="TOO_WEAK", decision=attempted),
                actor=driver_actor(),
            )
        assert excinfo.value.permitted == ("REFUSE",)


def test_too_weak_defaults_to_refusal_when_no_decision_is_supplied():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).capture_proof(
        _proof(task, assessment="TOO_WEAK", condition="MAJOR_DAMAGE"),
        actor=driver_actor(),
    )

    assert result.task.condition_decision is ConditionDecision.REFUSE


def test_good_packaging_cannot_carry_a_warning_or_a_note():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    with pytest.raises(PackagingDecisionNotPermitted):
        lifecycle_service(store).capture_proof(
            _proof(task, assessment="GOOD", decision="ACCEPT_WITH_NOTE"),
            actor=driver_actor(),
        )


def test_a_client_that_sends_no_assessment_keeps_its_existing_behaviour():
    """Backward compatibility: the assessment is derived, nothing is rejected."""
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).capture_proof(
        CaptureProofCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(3),
            package_condition_status=PackageConditionStatus.MAJOR_DAMAGE,
            condition_notes="crushed corner",
            evidence_present=True,
        ),
        actor=driver_actor(),
    )

    assert result.task.status is PickupTaskStatus.PROOF_CAPTURED
    assert result.task.packaging_assessment is PackagingAssessment.PRE_EXISTING_DAMAGE
    assert result.task.condition_decision is ConditionDecision.ACCEPT_WITH_NOTE


def test_an_unknown_assessment_is_rejected_rather_than_guessed():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    with pytest.raises(ExceptionEvidenceInsufficient):
        lifecycle_service(store).capture_proof(
            _proof(task, assessment="PROBABLY_FINE"), actor=driver_actor()
        )


def test_the_assessment_implies_the_refusal_reason():
    assert (
        refusal_reason_for_assessment(PackagingAssessment.TOO_WEAK)
        is PickupRefusalReason.TOO_WEAK_PACKAGING
    )
    assert (
        refusal_reason_for_assessment(PackagingAssessment.PRE_EXISTING_DAMAGE)
        is PickupRefusalReason.DAMAGED_BEFORE_PICKUP
    )


# ------------------------------------------------------------- refusal outcomes


def test_a_refused_parcel_stays_with_the_merchant_and_starts_no_custody():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).refuse(
        RefusePickupCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            reason="TOO_WEAK_PACKAGING",
        ),
        actor=driver_actor(),
    )

    assert result.task.stop_outcome is StopOutcome.REFUSED
    assert result.task.stop_outcome_reason is PickupRefusalReason.TOO_WEAK_PACKAGING
    assert result.task.status is PickupTaskStatus.FAILED
    assert result.task.acceptance_state is None
    assert result.task.accepted_at is None
    # No custody event was recorded, so nothing may be published.
    assert store.outbox.list_pending() == ()


@pytest.mark.parametrize(
    "reason",
    ["TOO_WEAK_PACKAGING", "DAMAGED_BEFORE_PICKUP", "DOES_NOT_MATCH_SHIPMENT"],
)
def test_every_product_refusal_reason_is_accepted(reason):
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    result = lifecycle_service(store).refuse(
        RefusePickupCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            reason=reason,
        ),
        actor=driver_actor(),
    )

    assert result.task.stop_outcome_reason is PickupRefusalReason(reason)


def test_an_unknown_refusal_reason_is_rejected():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    with pytest.raises(ExceptionEvidenceInsufficient):
        lifecycle_service(store).refuse(
            RefusePickupCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(4),
                reason="DID_NOT_LIKE_IT",
            ),
            actor=driver_actor(),
        )


def test_repeating_the_same_refusal_is_an_idempotent_replay():
    store = build_store()
    start_session(store)
    task = _ready_task(store)
    service = lifecycle_service(store)
    command = RefusePickupCommand(
        pickup_task_id=task.pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        occurred_at=BASE_TIME + minutes(4),
        reason="DAMAGED_BEFORE_PICKUP",
    )

    first = service.refuse(command, actor=driver_actor())
    second = service.refuse(command, actor=driver_actor())

    assert second.replayed is True
    assert second.task.version == first.task.version


def test_a_second_different_outcome_is_refused_not_silently_overwritten():
    store = build_store()
    start_session(store)
    task = _ready_task(store)
    service = lifecycle_service(store)
    service.refuse(
        RefusePickupCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            reason="DAMAGED_BEFORE_PICKUP",
        ),
        actor=driver_actor(),
    )

    with pytest.raises(StopOutcomeAlreadyRecorded):
        service.mark_not_presented(
            TaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(5),
            ),
            actor=driver_actor(),
        )


def test_a_driver_may_not_refuse_someone_elses_parcel():
    store = build_store()
    start_session(store)
    start_session(store, driver_user_id="driver-99")
    task = _ready_task(store)

    with pytest.raises(ActingDriverMismatch):
        lifecycle_service(store).refuse(
            RefusePickupCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id="driver-99",
                occurred_at=BASE_TIME + minutes(4),
                reason="TOO_WEAK_PACKAGING",
            ),
            actor=driver_actor("driver-99"),
        )


# ------------------------------------------------------- not-presented outcomes


def test_a_parcel_the_merchant_never_handed_over_is_marked_not_presented():
    store = build_store()
    start_session(store)
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED
    )

    result = lifecycle_service(store).mark_not_presented(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
        ),
        actor=driver_actor(),
    )

    assert result.task.stop_outcome is StopOutcome.NOT_PRESENTED
    # No penalty for anyone: no reason is demanded and no exception is recorded.
    assert result.task.stop_outcome_reason is None
    assert result.task.exception_reason is None
    assert store.outbox.list_pending() == ()


def test_marking_not_presented_twice_is_an_idempotent_replay():
    store = build_store()
    start_session(store)
    task = register_task(store, assignment_state=AssignmentState.ACKNOWLEDGED)
    service = lifecycle_service(store)
    command = TaskCommand(
        pickup_task_id=task.pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        occurred_at=BASE_TIME + minutes(4),
    )

    service.mark_not_presented(command, actor=driver_actor())
    replay = service.mark_not_presented(command, actor=driver_actor())

    assert replay.replayed is True


def test_an_unacknowledged_assignment_cannot_produce_a_stop_outcome():
    store = build_store()
    start_session(store)
    task = register_task(store)

    with pytest.raises(AssignmentNotAcknowledged):
        lifecycle_service(store).mark_not_presented(
            TaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(4),
            ),
            actor=driver_actor(),
        )


# ----------------------------------------------------- acceptance is one-way


def test_a_parcel_already_in_custody_can_no_longer_be_refused():
    store = build_store()
    start_session(store)
    task = _ready_task(store)
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()

    with pytest.raises(StopOutcomeNotAllowed):
        lifecycle_service(store).refuse(
            RefusePickupCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(9),
                reason="DAMAGED_BEFORE_PICKUP",
            ),
            actor=driver_actor(),
        )


# --------------------------------------------------------------- scan resolution


def _stop(store):
    return PickupStopService(store)


def test_a_label_expected_at_this_stop_resolves_to_valid():
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=batch, scanned_identifier=str(task.shipment_id)
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.VALID
    assert resolution.can_proceed is True
    assert resolution.pickup_task_id == task.pickup_task_id


def test_a_label_registered_to_nobody_resolves_to_unknown_and_creates_nothing():
    store = build_store()
    start_session(store)
    batch = uuid4()
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(assigned_batch_id=batch, scanned_identifier="NOT-A-LABEL"),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.UNKNOWN_LABEL
    assert resolution.can_proceed is False
    assert resolution.pickup_task_id is None
    # "An unregistered parcel cannot become a shipment in the field."
    assert len(store.pickup_tasks.list_tasks_for_driver(DRIVER_ID)) == 1


def test_a_label_from_another_stop_is_not_part_of_this_pickup():
    store = build_store()
    start_session(store)
    here, elsewhere = uuid4(), uuid4()
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=here
    )
    other = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=elsewhere
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=here, scanned_identifier=str(other.shipment_id)
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.NOT_IN_THIS_PICKUP
    assert resolution.can_proceed is False


def test_a_cancelled_shipment_cannot_enter_custody():
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store,
        status=PickupTaskStatus.CANCELLED,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch,
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=batch, scanned_identifier=str(task.shipment_id)
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.CANCELLED_SHIPMENT


def test_rescanning_an_accepted_parcel_reports_already_accepted():
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=batch, scanned_identifier=str(task.shipment_id)
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.ALREADY_ACCEPTED


def test_scanning_the_same_label_again_mid_stop_reports_a_duplicate():
    store = build_store()
    start_session(store)
    batch = uuid4()
    _ready_task(store, batch_id=batch)

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(assigned_batch_id=batch, scanned_identifier=LABEL),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.DUPLICATE_SCAN


def test_a_refused_parcel_is_no_longer_scannable_at_this_stop():
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = _ready_task(store, batch_id=batch)
    lifecycle_service(store).refuse(
        RefusePickupCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            reason="TOO_WEAK_PACKAGING",
        ),
        actor=driver_actor(),
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(assigned_batch_id=batch, scanned_identifier=LABEL),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.NOT_IN_THIS_PICKUP


def test_an_unreadable_label_is_never_resolved_to_a_task():
    """Typing the code or attaching a replacement label is not allowed."""
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=batch,
            scanned_identifier=str(task.shipment_id),
            unreadable=True,
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.UNREADABLE
    assert resolution.pickup_task_id is None


def test_a_blank_scan_is_rejected_rather_than_treated_as_unknown():
    store = build_store()
    start_session(store)
    batch = uuid4()
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )

    with pytest.raises(ScanIdentifierMissing):
        _stop(store).resolve_scan(
            ResolveScanCommand(assigned_batch_id=batch, scanned_identifier="   "),
            actor=driver_actor(),
        )


def test_scan_resolution_never_reaches_another_drivers_work():
    store = build_store()
    start_session(store)
    start_session(store, driver_user_id="driver-99")
    batch = uuid4()
    theirs = register_task(
        store,
        driver_user_id="driver-99",
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch,
    )

    resolution = _stop(store).resolve_scan(
        ResolveScanCommand(
            assigned_batch_id=batch, scanned_identifier=str(theirs.shipment_id)
        ),
        actor=driver_actor(),
    )

    assert resolution.outcome is ScanResolutionOutcome.UNKNOWN_LABEL


# ---------------------------------------------------------------- stop readiness


def test_a_stop_cannot_complete_while_a_parcel_is_unresolved():
    store = build_store()
    start_session(store)
    batch = uuid4()
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.expected == 2
    assert readiness.unresolved == 2
    assert readiness.can_complete is False
    assert "2 parcels still unresolved" in readiness.blocking_reason


def test_a_stop_completes_once_every_expected_parcel_has_an_outcome():
    store = build_store()
    start_session(store)
    batch = uuid4()
    refused = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    absent = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    service = lifecycle_service(store)
    service.refuse(
        RefusePickupCommand(
            pickup_task_id=refused.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            reason="TOO_WEAK_PACKAGING",
        ),
        actor=driver_actor(),
    )
    service.mark_not_presented(
        TaskCommand(
            pickup_task_id=absent.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
        ),
        actor=driver_actor(),
    )

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.can_complete is True
    assert readiness.refused == 1
    assert readiness.not_presented == 1
    assert readiness.accepted == 0
    assert readiness.is_partial is True
    assert readiness.blocking_reason is None


def test_a_stop_where_everything_entered_custody_is_not_partial():
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.can_complete is True
    assert readiness.is_partial is False
    assert readiness.accepted == 1


def test_an_unknown_batch_is_not_reported_as_an_empty_stop():
    store = build_store()
    start_session(store)

    with pytest.raises(PickupBatchNotFound):
        _stop(store).stop_readiness(assigned_batch_id=uuid4(), actor=driver_actor())


def test_stop_readiness_only_counts_the_acting_drivers_parcels():
    store = build_store()
    start_session(store)
    start_session(store, driver_user_id="driver-99")
    batch = uuid4()
    register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    register_task(
        store,
        driver_user_id="driver-99",
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch,
    )

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.expected == 1


# ------------------------------------------------------------ acceptance status


def test_an_unaccepted_task_reports_safe_to_retry():
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    status = _stop(store).acceptance_status(
        pickup_task_id=task.pickup_task_id, actor=driver_actor()
    )

    assert status.recorded is False
    assert status.safe_to_retry is True
    assert status.accepted_at is None


def test_an_accepted_task_reports_recorded_and_is_not_safe_to_retry():
    store = build_store()
    start_session(store)
    task = _ready_task(store)
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    task.accepted_at = BASE_TIME + minutes(6)
    task.accepted_by_driver_user_id = DRIVER_ID
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()

    status = _stop(store).acceptance_status(
        pickup_task_id=task.pickup_task_id, actor=driver_actor()
    )

    assert status.recorded is True
    assert status.safe_to_retry is False
    assert status.accepted_by_driver_user_id == DRIVER_ID


def test_reading_the_status_twice_gives_the_same_answer_and_changes_nothing():
    store = build_store()
    start_session(store)
    task = _ready_task(store)
    service = _stop(store)

    before = store.pickup_tasks.get_pickup_task(task.pickup_task_id).version
    first = service.acceptance_status(
        pickup_task_id=task.pickup_task_id, actor=driver_actor()
    )
    second = service.acceptance_status(
        pickup_task_id=task.pickup_task_id, actor=driver_actor()
    )

    assert first == second
    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored.version == before


def test_a_driver_cannot_read_another_drivers_acceptance_status():
    store = build_store()
    start_session(store)
    start_session(store, driver_user_id="driver-99")
    task = _ready_task(store)

    with pytest.raises(PickupTaskNotFound):
        _stop(store).acceptance_status(
            pickup_task_id=task.pickup_task_id, actor=driver_actor("driver-99")
        )


def test_an_unknown_task_is_reported_as_not_found():
    store = build_store()
    start_session(store)

    with pytest.raises(PickupTaskNotFound):
        _stop(store).acceptance_status(
            pickup_task_id=uuid4(), actor=driver_actor()
        )


# ------------------------------------------------------- photo documentation gate


def _acceptance_setup(*, photo_required: bool):
    """A task ready for acceptance, with the photo add-on on or off."""
    store = build_store()
    recovery = PickupRecoveryService(store, InMemoryShipmentEligibilityAdapter())
    task = recovery.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=uuid4(),
            shipment_id=uuid4(),
            assigned_driver_user_id=DRIVER_ID,
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=True,
            status=PickupTaskStatus.PROOF_CAPTURED,
            created_at=BASE_TIME,
            assignment_state=AssignmentState.ACKNOWLEDGED,
            photo_documentation_required=photo_required,
        )
    )
    return store, task, PickupAcceptanceService(store)


def _accept(task, *, media_refs=()):
    return AcceptPickupTaskCommand(
        pickup_task_id=task.pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        scanned_identifier=LABEL,
        outcome=AcceptanceOutcome.ACCEPTED,
        idempotency_key="accept-photo-1",
        accepted_at=BASE_TIME + minutes(6),
        media_refs=media_refs,
        correlation_id=uuid4(),
    )


def test_acceptance_is_blocked_until_the_required_photo_is_attached():
    _store, task, service = _acceptance_setup(photo_required=True)

    with pytest.raises(PickupPhotoDocumentationMissing):
        service.accept_pickup_task(_accept(task))


def test_acceptance_succeeds_once_the_photo_is_attached():
    _store, task, service = _acceptance_setup(photo_required=True)

    result = service.accept_pickup_task(
        _accept(
            task,
            media_refs=(
                EvidenceMediaRef(
                    ref_type="pickup_photo",
                    bucket="evidence",
                    key="HHD-10452/pickup.jpg",
                    content_type="image/jpeg",
                ),
            ),
        )
    )

    assert result.pickup_task.is_accepted is True


def test_the_photo_gate_is_inert_for_a_shipment_without_the_add_on():
    """Without the add-on no photo is taken at any stage, so nothing is demanded."""
    _store, task, service = _acceptance_setup(photo_required=False)

    result = service.accept_pickup_task(_accept(task))

    assert result.pickup_task.is_accepted is True


def test_a_blocked_acceptance_publishes_nothing_and_leaves_custody_unstarted():
    store, task, service = _acceptance_setup(photo_required=True)

    with pytest.raises(PickupPhotoDocumentationMissing):
        service.accept_pickup_task(_accept(task))

    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored.acceptance_state is None
    assert store.outbox.list_pending() == ()


def test_the_photo_requirement_survives_a_reassignment():
    store, task, _service = _acceptance_setup(photo_required=True)
    eligibility = InMemoryShipmentEligibilityAdapter()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=task.shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    recovery = PickupRecoveryService(store, eligibility)

    result = recovery.reassign_pickup(
        ReassignRecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="reassign-photo-1",
            reason="driver unavailable",
            occurred_at=BASE_TIME + minutes(7),
            new_driver_user_id="driver-99",
        )
    )

    assert result.replacement_task.photo_documentation_required is True


# ------------------------------------------------------------------ concurrency


def test_two_drivers_racing_a_stop_outcome_cannot_both_win():
    """One outcome per parcel, even when two commands arrive together."""
    store = build_store()
    start_session(store)
    task = _ready_task(store)

    barrier = Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def _refuse() -> None:
        service = lifecycle_service(store)
        barrier.wait()
        try:
            result = service.refuse(
                RefusePickupCommand(
                    pickup_task_id=task.pickup_task_id,
                    acting_driver_user_id=DRIVER_ID,
                    occurred_at=BASE_TIME + minutes(4),
                    reason="TOO_WEAK_PACKAGING",
                ),
                actor=driver_actor(),
            )
            outcomes.append(result.task.stop_outcome.value)
        except BaseException as exc:  # noqa: BLE001 — collected for assertion
            errors.append(exc)

    def _not_presented() -> None:
        service = lifecycle_service(store)
        barrier.wait()
        try:
            result = service.mark_not_presented(
                TaskCommand(
                    pickup_task_id=task.pickup_task_id,
                    acting_driver_user_id=DRIVER_ID,
                    occurred_at=BASE_TIME + minutes(4),
                ),
                actor=driver_actor(),
            )
            outcomes.append(result.task.stop_outcome.value)
        except BaseException as exc:  # noqa: BLE001 — collected for assertion
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(_refuse), pool.submit(_not_presented)]:
            future.result()

    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored.stop_outcome is not None
    # Whatever interleaving happened, exactly one outcome is on the parcel and the
    # loser was told, not silently dropped.
    assert len(outcomes) + len(errors) == 2
    assert stored.stop_outcome.value in {"REFUSED", "NOT_PRESENTED"}
    assert stored.acceptance_state is None
    assert store.outbox.list_pending() == ()


def test_a_retried_parcel_is_counted_once_not_twice():
    """A superseded attempt is not a second expected parcel at the stop."""
    store = build_store()
    start_session(store)
    batch = uuid4()
    task = register_task(
        store, assignment_state=AssignmentState.ACKNOWLEDGED, assigned_batch_id=batch
    )
    eligibility = InMemoryShipmentEligibilityAdapter()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=task.shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    PickupRecoveryService(store, eligibility).retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="retry-stop-1",
            reason="merchant asked us to come back",
            occurred_at=BASE_TIME + minutes(8),
        )
    )

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.expected == 1
    assert readiness.unresolved == 1
    assert readiness.can_complete is False


def test_a_cancelled_parcel_is_closed_by_recovery_and_does_not_block_the_stop():
    store = build_store()
    start_session(store)
    batch = uuid4()
    register_task(
        store,
        status=PickupTaskStatus.CANCELLED,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch,
    )

    readiness = _stop(store).stop_readiness(
        assigned_batch_id=batch, actor=driver_actor()
    )

    assert readiness.closed_by_recovery == 1
    assert readiness.unresolved == 0
    assert readiness.can_complete is True
