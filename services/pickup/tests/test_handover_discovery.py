"""Sender-facing discovery of the handover ceremony.

Every state here is reached by driving the real driver and sender commands, never by
writing ceremony rows directly — the projection is only worth anything if it agrees
with what the ceremony actually did.
"""

from __future__ import annotations

from uuid import uuid4

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
from pickup.application.handover_discovery_service import (
    HandoverDiscoveryService,
    HandoverDiscoveryState,
)
from pickup.application.handover_verification_service import (
    ConfirmManifestCommand,
    IssueChallengeCommand,
    SubmitManifestCommand,
    VerifyChallengeCommand,
)
from pickup.application.task_lifecycle_service import (
    CaptureProofCommand,
    ReportExceptionCommand,
    ScanTaskCommand,
    TaskCommand,
)
from pickup.domain.value_objects import (
    AssignmentState,
    PickupExceptionReason,
    PickupTaskStatus,
)

IDENTIFIER = "WB-2001"


def _discovery(store, *, verification_required: bool = True) -> HandoverDiscoveryService:
    return HandoverDiscoveryService(store, verification_required=verification_required)


def _progressed_task(store, *, shipment_id=None):
    """Drive one task to PROOF_CAPTURED through the real driver commands."""
    start_session(store)
    task = register_task(store, shipment_id=shipment_id)
    service = lifecycle_service(store)
    for step, at in (("acknowledge", 1), ("arrive", 2)):
        getattr(service, step)(
            TaskCommand(
                pickup_task_id=task.pickup_task_id,
                acting_driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(at),
            ),
            actor=driver_actor(),
        )
    service.scan(
        ScanTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(3),
            scanned_identifier=IDENTIFIER,
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


def _verify(store, task, service):
    issued = service.issue_challenge(
        IssueChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
        ),
        actor=driver_actor(),
    )
    service.verify_challenge(
        VerifyChallengeCommand(
            pickup_task_id=task.pickup_task_id,
            presented_payload=issued.payload,
            occurred_at=BASE_TIME + minutes(6),
        ),
        actor=merchant_actor(),
    )
    return issued


def _submit(store, task, service):
    return service.submit_manifest(
        SubmitManifestCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=IDENTIFIER,
            occurred_at=BASE_TIME + minutes(7),
        ),
        actor=driver_actor(),
    )


# ------------------------------------------------------------------- resolution


def test_a_shipment_with_no_pickup_task_discovers_nothing() -> None:
    store = build_store()
    assert _discovery(store).describe_for_shipment(uuid4()) is None


def test_discovery_keys_on_the_shipment_not_the_pickup_task() -> None:
    store = build_store()
    shipment_id = uuid4()
    task = _progressed_task(store, shipment_id=shipment_id)
    view = _discovery(store).describe_for_shipment(shipment_id)
    assert view is not None
    assert view.shipment_id == shipment_id
    assert view.pickup_task_id == task.pickup_task_id


def test_a_failed_attempt_with_no_successor_discovers_nothing() -> None:
    """Recovery's business, not a ceremony the sender can still act on."""
    store = build_store()
    task = _progressed_task(store)
    lifecycle_service(store).fail(
        ReportExceptionCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
            reason=PickupExceptionReason.SENDER_ABSENT,
            notes="nobody at the address",
            contact_attempted=True,
        ),
        actor=driver_actor(),
    )
    assert _discovery(store).describe_for_shipment(task.shipment_id) is None


# ------------------------------------------------------------------ ceremony states


def test_before_verification_the_sender_may_verify_the_courier() -> None:
    store = build_store()
    task = _progressed_task(store)
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.AWAITING_COURIER_VERIFICATION
    assert view.verification_required is True
    assert view.actionable is True
    assert view.can_verify_courier is True
    assert view.can_confirm_manifest is False


def test_after_verification_neither_half_is_offered_until_a_manifest_arrives() -> None:
    store = build_store()
    task = _progressed_task(store)
    _verify(store, task, verification_service(store))
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.COURIER_VERIFIED
    assert view.actionable is True
    assert view.can_verify_courier is False
    assert view.can_confirm_manifest is False


def test_a_submitted_manifest_offers_confirmation_to_the_sender() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verify(store, task, service)
    _submit(store, task, service)
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.AWAITING_MANIFEST_CONFIRMATION
    assert view.can_confirm_manifest is True
    assert view.can_verify_courier is False


def test_a_confirmed_manifest_leaves_the_sender_nothing_to_do() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verify(store, task, service)
    _submit(store, task, service)
    service.confirm_manifest(
        ConfirmManifestCommand(
            pickup_task_id=task.pickup_task_id,
            occurred_at=BASE_TIME + minutes(8),
        ),
        actor=merchant_actor(),
    )
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.READY_FOR_ACCEPTANCE
    # Still open — custody has not started — but both halves are done.
    assert view.actionable is True
    assert view.can_verify_courier is False
    assert view.can_confirm_manifest is False


def test_acceptance_closes_discovery() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    _verify(store, task, service)
    _submit(store, task, service)
    service.confirm_manifest(
        ConfirmManifestCommand(
            pickup_task_id=task.pickup_task_id,
            occurred_at=BASE_TIME + minutes(8),
        ),
        actor=merchant_actor(),
    )
    PickupAcceptanceService(store, verification_gate=service).accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            scanned_identifier=IDENTIFIER,
            outcome="ACCEPTED",
            idempotency_key="discovery-accept-1",
            accepted_at=BASE_TIME + minutes(10),
        )
    )
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.COMPLETED
    assert view.actionable is False
    assert view.can_verify_courier is False
    assert view.can_confirm_manifest is False


def test_discovery_reports_not_required_when_the_ceremony_is_switched_off() -> None:
    store = build_store()
    task = _progressed_task(store)
    view = _discovery(store, verification_required=False).describe_for_shipment(
        task.shipment_id
    )
    assert view is not None
    assert view.state is HandoverDiscoveryState.NOT_REQUIRED
    assert view.verification_required is False
    assert view.actionable is False
    assert view.can_verify_courier is False


# ------------------------------------------------------------------ flag guards


def test_an_unacknowledged_assignment_offers_no_sender_action() -> None:
    """No challenge can be issued before acknowledgement, so none is promised."""
    store = build_store()
    start_session(store)
    task = register_task(store, assignment_state=AssignmentState.OFFERED)
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.state is HandoverDiscoveryState.AWAITING_COURIER_VERIFICATION
    assert view.actionable is True
    assert view.can_verify_courier is False


def test_an_exception_reported_task_stops_offering_the_sender_half() -> None:
    """EXCEPTION_REPORTED is still live work, but no longer challenge-eligible."""
    store = build_store()
    task = _progressed_task(store)
    lifecycle_service(store).report_exception(
        ReportExceptionCommand(
            pickup_task_id=task.pickup_task_id,
            acting_driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(5),
            reason=PickupExceptionReason.PACKAGE_NOT_READY,
            notes="parcel not packed yet",
        ),
        actor=driver_actor(),
    )
    reloaded = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert reloaded is not None
    assert reloaded.status is PickupTaskStatus.EXCEPTION_REPORTED
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    assert view.can_verify_courier is False


def test_discovery_never_carries_the_challenge_payload() -> None:
    store = build_store()
    task = _progressed_task(store)
    service = verification_service(store)
    issued = _verify(store, task, service)
    view = _discovery(store).describe_for_shipment(task.shipment_id)
    assert view is not None
    secret = issued.payload.split("s=")[1]
    assert secret not in repr(view)
    assert DRIVER_ID not in repr(view)
