"""Sender handover ceremony: dynamic challenge, parcel manifest, acceptance gate."""

from __future__ import annotations

import pytest
from driver_fixtures import (
    BASE_TIME,
    DRIVER_ID,
    build_store,
    driver_actor,
    lifecycle_service,
    merchant_actor,
    minutes,
    register_task,
    start_session,
    verification_service,
)

from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.handover_verification_service import (
    ConfirmManifestCommand,
    IssueChallengeCommand,
    SubmitManifestCommand,
    VerificationPolicy,
    VerifyChallengeCommand,
)
from pickup.application.task_lifecycle_service import (
    CaptureProofCommand,
    ScanTaskCommand,
    TaskCommand,
)
from pickup.domain.errors import (
    ActingDriverMismatch,
    CourierChallengeAlreadyUsed,
    CourierChallengeExpired,
    CourierChallengeInvalidated,
    CourierChallengeIssueBlocked,
    CourierChallengeNotFound,
    CourierManifestMismatch,
    CourierManifestNotSubmitted,
    CourierManifestRequired,
    CourierSecretInvalid,
    CourierVerificationRateLimited,
    CourierVerificationRequired,
    SenderMayNotVerifyOwnCourier,
)
from pickup.domain.handover import (
    CourierChallengeStatus,
    CourierManifestStatus,
    SenderType,
    VerificationInvalidationReason,
)
from pickup.domain.security import build_manifest_digest
from pickup.domain.value_objects import AssignmentState, PickupTaskAcceptanceState

IDENTIFIER = "WB-1001"


def _progressed_task(store, *, identifier: str = IDENTIFIER):
    """Drive a task to PROOF_CAPTURED through the real driver commands."""
    start_session(store)
    task = register_task(store)
    service = lifecycle_service(store)
    service.acknowledge(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(1),
        ),
        actor=driver_actor(),
    )
    service.arrive(
        TaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(2),
        ),
        actor=driver_actor(),
    )
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(3),
            scanned_identifier=identifier,
        ),
        actor=driver_actor(),
    )
    service.capture_proof(
        CaptureProofCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(4),
            package_condition_status="GOOD",
        ),
        actor=driver_actor(),
    )
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    return reloaded


def _issue(store, task, *, at_minutes: int = 5, service=None):
    service = service or verification_service(store)
    return service.issue_challenge(
        IssueChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(at_minutes),
        ),
        actor=driver_actor(),
    )


# ----------------------------------------------------------------- challenge


def test_issue_returns_the_secret_exactly_once_and_stores_only_a_hash() -> None:
    store = build_store()
    task = _progressed_task(store)
    issued = _issue(store, task)
    assert issued.payload.startswith("hudhud://pickup/verify?v=1&s=")
    stored = store.challenges.get_challenge(issued.challenge_id)
    assert stored is not None
    assert issued.payload.split("s=")[1] not in stored.secret_hash
    assert stored.status is CourierChallengeStatus.ISSUED


def test_only_the_assigned_courier_may_issue() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    with pytest.raises(ActingDriverMismatch):
        service.issue_challenge(
            IssueChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id="driver-99",
                occurred_at=BASE_TIME + minutes(5),
            ),
            actor=driver_actor("driver-99"),
        )


def test_issue_requires_an_acknowledged_assignment() -> None:
    store = build_store()
    start_session(store)
    task = register_task(store, assignment_state=AssignmentState.OFFERED)
    with pytest.raises(CourierChallengeIssueBlocked):
        _issue(store, task)


def test_reissue_supersedes_the_previous_issued_challenge() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    first = _issue(store, task, service=service)
    second = _issue(store, task, at_minutes=6, service=service)
    assert second.reissued is True
    stale = store.challenges.get_challenge(first.challenge_id)
    assert stale is not None
    assert stale.status is CourierChallengeStatus.INVALIDATED
    assert stale.invalidation_reason is VerificationInvalidationReason.REISSUED


def test_reissue_is_blocked_once_the_sender_has_verified() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    with pytest.raises(CourierChallengeIssueBlocked):
        _issue(store, task, at_minutes=7, service=service)


# -------------------------------------------------------------- verification


def test_sender_verification_binds_the_courier_and_sets_a_validity_window() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    result = service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    assert result.status is CourierChallengeStatus.VERIFIED
    assert result.courier_user_id == DRIVER_ID
    assert result.valid_until > result.verified_at
    stored = store.challenges.get_challenge(issued.challenge_id)
    assert stored is not None
    assert stored.sender_type is SenderType.MERCHANT


def test_the_assigned_courier_can_never_verify_itself() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    with pytest.raises(SenderMayNotVerifyOwnCourier):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload=issued.payload,
                occurred_at=BASE_TIME + minutes(6),
            ),
            actor=driver_actor(),
        )


def test_a_wrong_secret_is_rejected_and_the_attempt_is_counted() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    with pytest.raises(CourierSecretInvalid):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload="hudhud://pickup/verify?v=1&s=not-the-secret",
                occurred_at=BASE_TIME + minutes(6),
            ),
            actor=merchant_actor(),
        )
    stored = store.challenges.get_challenge(issued.challenge_id)
    assert stored is not None
    assert stored.failed_attempt_count == 1
    assert stored.status is CourierChallengeStatus.ISSUED


def test_repeated_wrong_secrets_lock_the_challenge_out() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(
        store,
        policy=VerificationPolicy(
            challenge_ttl_seconds=3600,
            max_failed_attempts=2,
            lockout_seconds=600,
        ),
    )
    _issue(store, task, service=service)
    for attempt in range(2):
        with pytest.raises(CourierSecretInvalid):
            service.verify_challenge(
                VerifyChallengeCommand(
                    pickup_task_id=task.pickup_task_id,
                    presented_payload="hudhud://pickup/verify?v=1&s=wrong",
                    occurred_at=BASE_TIME + minutes(6 + attempt),
                ),
                actor=merchant_actor(),
            )
    with pytest.raises(CourierVerificationRateLimited):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload="hudhud://pickup/verify?v=1&s=wrong",
                occurred_at=BASE_TIME + minutes(8),
            ),
            actor=merchant_actor(),
        )


def test_an_expired_challenge_is_marked_expired_and_refused() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store, policy=VerificationPolicy(challenge_ttl_seconds=60))
    issued = _issue(store, task, service=service)
    with pytest.raises(CourierChallengeExpired):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload=issued.payload,
                occurred_at=BASE_TIME + minutes(30),
            ),
            actor=merchant_actor(),
        )
    stored = store.challenges.get_challenge(issued.challenge_id)
    assert stored is not None
    assert stored.status is CourierChallengeStatus.EXPIRED


def test_verification_replay_by_the_same_sender_returns_the_same_window() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    command = VerifyChallengeCommand(
        pickup_task_id=task.pickup_task_id,
        presented_payload=issued.payload,
        occurred_at=BASE_TIME + minutes(6),
    )
    first = service.verify_challenge(command, actor=merchant_actor())
    second = service.verify_challenge(command, actor=merchant_actor())
    assert second.replayed is True
    assert second.valid_until == first.valid_until


def test_a_different_sender_cannot_learn_a_challenge_is_verified() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    with pytest.raises(CourierChallengeAlreadyUsed):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload=issued.payload,
                occurred_at=BASE_TIME + minutes(7),
            ),
            actor=merchant_actor("merchant-user-other"),
        )


def test_verify_without_a_challenge_is_not_found() -> None:
    store = build_store()
    task = _progressed_task(store)
    with pytest.raises(CourierChallengeNotFound):
        verification_service(store).verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload="hudhud://pickup/verify?v=1&s=anything",
                occurred_at=BASE_TIME + minutes(6),
            ),
            actor=merchant_actor(),
        )


def test_reassignment_invalidates_the_whole_ceremony() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _issue(store, task, service=service)
    service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    store.begin()
    challenge_ids, _ = service.invalidate_for_task(
        pickup_task_id=task.pickup_task_id,
        reason=VerificationInvalidationReason.REASSIGNED,
        now=BASE_TIME + minutes(7),
    )
    store.commit()
    assert issued.challenge_id in challenge_ids
    stored = store.challenges.get_challenge(issued.challenge_id)
    assert stored is not None
    assert stored.status is CourierChallengeStatus.INVALIDATED
    with pytest.raises(CourierChallengeInvalidated):
        service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=task.pickup_task_id,
                presented_payload=issued.payload,
                challenge_id=issued.challenge_id,
                occurred_at=BASE_TIME + minutes(8),
            ),
            actor=merchant_actor(),
        )


# ------------------------------------------------------------------ manifest


def _verified(store, task, service):
    issued = _issue(store, task, service=service)
    service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    return issued


def _submit(store, task, service, *, identifier: str = IDENTIFIER, at_minutes: int = 7):
    return service.submit_manifest(
        SubmitManifestCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=identifier,
            occurred_at=BASE_TIME + minutes(at_minutes),
        ),
        actor=driver_actor(),
    )


def test_manifest_digest_binds_the_shipment_and_the_scanned_parcel() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    result = _submit(store, task, service)
    assert result.manifest.manifest_digest == build_manifest_digest(
        shipment_id=task.shipment_id,
        scanned_identifier=IDENTIFIER,
    )


def test_resubmitting_the_same_manifest_is_a_replay() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _submit(store, task, service)
    again = _submit(store, task, service, at_minutes=8)
    assert again.replayed is True


def test_submitting_a_different_parcel_is_rejected_against_the_scan() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    with pytest.raises(CourierManifestMismatch):
        _submit(store, task, service, identifier="WB-DIFFERENT")


def test_confirmation_requires_a_live_verified_challenge() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _submit(store, task, service)
    with pytest.raises(CourierVerificationRequired):
        service.confirm_manifest(
            ConfirmManifestCommand(
                pickup_task_id=task.pickup_task_id,
                occurred_at=BASE_TIME + minutes(9),
            ),
            actor=merchant_actor(),
        )


def test_confirmation_requires_a_submitted_manifest() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verified(store, task, service)
    with pytest.raises(CourierManifestNotSubmitted):
        service.confirm_manifest(
            ConfirmManifestCommand(
                pickup_task_id=task.pickup_task_id,
                occurred_at=BASE_TIME + minutes(9),
            ),
            actor=merchant_actor(),
        )


def test_the_courier_cannot_confirm_its_own_manifest() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verified(store, task, service)
    _submit(store, task, service)
    with pytest.raises(SenderMayNotVerifyOwnCourier):
        service.confirm_manifest(
            ConfirmManifestCommand(
                pickup_task_id=task.pickup_task_id,
                occurred_at=BASE_TIME + minutes(9),
            ),
            actor=driver_actor(),
        )


def test_confirmation_records_the_sender_and_a_validity_window() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verified(store, task, service)
    _submit(store, task, service)
    confirmed = service.confirm_manifest(
        ConfirmManifestCommand(
            pickup_task_id=task.pickup_task_id,
            occurred_at=BASE_TIME + minutes(9),
        ),
        actor=merchant_actor(),
    )
    assert confirmed.manifest.status is CourierManifestStatus.CONFIRMED
    assert confirmed.manifest.confirmed_by_user_id == merchant_actor().actor_id
    assert confirmed.manifest.confirmation_valid_until is not None


# ------------------------------------------------------------ acceptance gate


def _ready_for_acceptance(store):
    task = _progressed_task(store)
    service = verification_service(store)
    _verified(store, task, service)
    _submit(store, task, service)
    service.confirm_manifest(
        ConfirmManifestCommand(
            pickup_task_id=task.pickup_task_id,
            occurred_at=BASE_TIME + minutes(9),
        ),
        actor=merchant_actor(),
    )
    return task, service


def test_acceptance_without_the_ceremony_is_refused() -> None:
    store = build_store()
    task = _progressed_task(store)
    gate = verification_service(store)
    acceptance = PickupAcceptanceService(store, verification_gate=gate)
    with pytest.raises(CourierVerificationRequired):
        acceptance.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                scanned_identifier=IDENTIFIER,
                outcome="ACCEPTED",
                idempotency_key="accept-1",
                accepted_at=BASE_TIME + minutes(10),
            )
        )


def test_acceptance_with_a_verified_challenge_but_no_manifest_is_refused() -> None:
    store = build_store()
    task = _progressed_task(store)
    gate = verification_service(store)
    _verified(store, task, gate)
    acceptance = PickupAcceptanceService(store, verification_gate=gate)
    with pytest.raises(CourierManifestRequired):
        acceptance.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                scanned_identifier=IDENTIFIER,
                outcome="ACCEPTED",
                idempotency_key="accept-2",
                accepted_at=BASE_TIME + minutes(10),
            )
        )


def test_acceptance_with_a_complete_ceremony_starts_custody_and_burns_it() -> None:
    store = build_store()
    task, gate = _ready_for_acceptance(store)
    acceptance = PickupAcceptanceService(store, verification_gate=gate)
    result = acceptance.accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=IDENTIFIER,
            outcome="ACCEPTED",
            idempotency_key="accept-3",
            accepted_at=BASE_TIME + minutes(10),
        )
    )
    assert result.pickup_task.acceptance_state is PickupTaskAcceptanceState.ACCEPTED
    assert result.outbox_record.event_type == "pickup.fact.accepted"
    challenges = store.challenges.list_open_for_task(task.pickup_task_id)
    manifests = store.courier_manifests.list_open_for_task(task.pickup_task_id)
    assert challenges == ()
    assert manifests == ()


def test_accepting_a_different_parcel_than_the_sender_confirmed_is_refused() -> None:
    store = build_store()
    task, gate = _ready_for_acceptance(store)
    acceptance = PickupAcceptanceService(store, verification_gate=gate)
    with pytest.raises(CourierManifestMismatch):
        acceptance.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                scanned_identifier="WB-SOMETHING-ELSE",
                outcome="ACCEPTED",
                idempotency_key="accept-4",
                accepted_at=BASE_TIME + minutes(10),
            )
        )


def test_one_ceremony_authorizes_exactly_one_acceptance() -> None:
    store = build_store()
    task, gate = _ready_for_acceptance(store)
    acceptance = PickupAcceptanceService(store, verification_gate=gate)
    acceptance.accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=IDENTIFIER,
            outcome="ACCEPTED",
            idempotency_key="accept-5",
            accepted_at=BASE_TIME + minutes(10),
        )
    )
    # A different idempotency key must not slip a second custody start through.
    with pytest.raises(Exception) as excinfo:
        acceptance.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                scanned_identifier=IDENTIFIER,
                outcome="ACCEPTED",
                idempotency_key="accept-6",
                accepted_at=BASE_TIME + minutes(11),
            )
        )
    assert "already accepted" in str(excinfo.value)
