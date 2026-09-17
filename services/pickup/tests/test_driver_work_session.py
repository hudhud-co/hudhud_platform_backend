"""Driver pickup-capability work session lifecycle and release blockers."""

from __future__ import annotations

from uuid import uuid4

import pytest
from driver_fixtures import (
    BASE_TIME,
    DRIVER_ID,
    build_store,
    driver_actor,
    hub_actor,
    hub_handover_service,
    lifecycle_service,
    minutes,
    register_task,
    start_session,
    work_session_service,
)

from pickup.application.driver_session_service import (
    EndWorkSessionCommand,
    PauseWorkSessionCommand,
    ResumeWorkSessionCommand,
    StartWorkSessionCommand,
)
from pickup.application.hub_handover_service import (
    CloseManifestCommand,
    CreateHandoverManifestCommand,
    ManifestLifecycleCommand,
    RecordHubReceiptCommand,
)
from pickup.application.task_lifecycle_service import TaskCommand
from pickup.domain.errors import (
    DriverWorkSessionAlreadyOpen,
    DriverWorkSessionBlocked,
    DriverWorkSessionNotFound,
    DriverWorkSessionNotOpen,
    InvalidWorkSessionReason,
)
from pickup.domain.handover import HandoverManifestItemStatus
from pickup.domain.value_objects import (
    AssignmentState,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
)
from pickup.domain.workforce import (
    DriverAvailabilityStatus,
    WorkSessionBlocker,
    WorkSessionStatus,
)


def test_start_derives_online_availability() -> None:
    store = build_store()
    result = start_session(store)
    assert result.session.status is WorkSessionStatus.ACTIVE
    assert result.session.availability is DriverAvailabilityStatus.ONLINE
    assert result.replayed is False


def test_repeat_start_is_an_idempotent_replay_not_a_second_session() -> None:
    store = build_store()
    first = start_session(store)
    second = start_session(store, at=BASE_TIME + minutes(5))
    assert second.replayed is True
    assert second.session.session_id == first.session.session_id


def test_start_while_paused_is_rejected_so_the_driver_resumes_instead() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    service.pause(
        PauseWorkSessionCommand(
            driver_user_id=DRIVER_ID,
            session_id=started.session.session_id,
            reason="BREAK",
            occurred_at=BASE_TIME + minutes(10),
        ),
        actor=driver_actor(),
    )
    with pytest.raises(DriverWorkSessionAlreadyOpen):
        service.start(
            StartWorkSessionCommand(
                driver_user_id=DRIVER_ID, occurred_at=BASE_TIME + minutes(11)
            ),
            actor=driver_actor(),
        )


def test_pause_then_resume_round_trips_availability() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    paused = service.pause(
        PauseWorkSessionCommand(
            driver_user_id=DRIVER_ID,
            session_id=started.session.session_id,
            reason="PRAYER",
            occurred_at=BASE_TIME + minutes(10),
        ),
        actor=driver_actor(),
    )
    assert paused.session.availability is DriverAvailabilityStatus.ON_BREAK
    resumed = service.resume(
        ResumeWorkSessionCommand(
            driver_user_id=DRIVER_ID,
            session_id=started.session.session_id,
            occurred_at=BASE_TIME + minutes(20),
        ),
        actor=driver_actor(),
    )
    assert resumed.session.availability is DriverAvailabilityStatus.ONLINE
    assert resumed.session.pause_reason is None


def test_repeated_pause_and_resume_are_replays() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    command = PauseWorkSessionCommand(
        driver_user_id=DRIVER_ID,
        session_id=started.session.session_id,
        reason="BREAK",
        occurred_at=BASE_TIME + minutes(10),
    )
    service.pause(command, actor=driver_actor())
    assert service.pause(command, actor=driver_actor()).replayed is True
    resume = ResumeWorkSessionCommand(
        driver_user_id=DRIVER_ID,
        session_id=started.session.session_id,
        occurred_at=BASE_TIME + minutes(20),
    )
    service.resume(resume, actor=driver_actor())
    assert service.resume(resume, actor=driver_actor()).replayed is True


def test_resume_requires_a_paused_session() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    service.end(
        EndWorkSessionCommand(
            driver_user_id=DRIVER_ID,
            session_id=started.session.session_id,
            reason="SESSION_COMPLETE",
            occurred_at=BASE_TIME + minutes(30),
        ),
        actor=driver_actor(),
    )
    with pytest.raises(DriverWorkSessionNotOpen):
        service.resume(
            ResumeWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                occurred_at=BASE_TIME + minutes(31),
            ),
            actor=driver_actor(),
        )


def test_another_driver_cannot_touch_the_session() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    with pytest.raises(DriverWorkSessionNotFound):
        service.pause(
            PauseWorkSessionCommand(
                driver_user_id="driver-99",
                session_id=started.session.session_id,
                reason="BREAK",
                occurred_at=BASE_TIME + minutes(10),
            ),
            actor=driver_actor("driver-99"),
        )


def test_other_reason_requires_notes() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    with pytest.raises(InvalidWorkSessionReason):
        service.pause(
            PauseWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                reason="OTHER",
                occurred_at=BASE_TIME + minutes(5),
            ),
            actor=driver_actor(),
        )


def test_unknown_reason_is_rejected() -> None:
    store = build_store()
    service = work_session_service(store)
    started = start_session(store)
    with pytest.raises(InvalidWorkSessionReason):
        service.end(
            EndWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                reason="TELEPORTED",
                occurred_at=BASE_TIME + minutes(5),
            ),
            actor=driver_actor(),
        )


def test_end_blocked_while_a_parcel_is_still_in_driver_custody() -> None:
    store = build_store()
    started = start_session(store)
    task = register_task(
        store, status=PickupTaskStatus.PROOF_CAPTURED, assignment_state=AssignmentState.ACKNOWLEDGED
    )
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()

    service = work_session_service(store)
    with pytest.raises(DriverWorkSessionBlocked) as excinfo:
        service.end(
            EndWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                reason="SESSION_COMPLETE",
                occurred_at=BASE_TIME + minutes(60),
            ),
            actor=driver_actor(),
        )
    assert WorkSessionBlocker.OPEN_PICKUP_CUSTODY.value in excinfo.value.blockers


def test_end_blocked_while_an_assignment_is_still_in_progress() -> None:
    store = build_store()
    started = start_session(store)
    task = register_task(store, status=PickupTaskStatus.PENDING)
    lifecycle_service(store).acknowledge(
        _task_command(task.pickup_task_id), actor=driver_actor()
    )
    service = work_session_service(store)
    with pytest.raises(DriverWorkSessionBlocked) as excinfo:
        service.end(
            EndWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                reason="SESSION_COMPLETE",
                occurred_at=BASE_TIME + minutes(60),
            ),
            actor=driver_actor(),
        )
    assert WorkSessionBlocker.ACTIVE_ASSIGNED_TASK.value in excinfo.value.blockers


def test_custody_released_at_hub_unblocks_ending_the_session() -> None:
    store = build_store()
    started = start_session(store)
    task = _accepted_task(store)
    hub_id = _release_at_hub(store, task)

    service = work_session_service(store)
    ended = service.end(
        EndWorkSessionCommand(
            driver_user_id=DRIVER_ID,
            session_id=started.session.session_id,
            reason="SESSION_COMPLETE",
            occurred_at=BASE_TIME + minutes(120),
        ),
        actor=driver_actor(),
    )
    assert ended.session.status is WorkSessionStatus.ENDED
    assert ended.session.availability is DriverAvailabilityStatus.OFFLINE
    assert hub_id is not None


def test_missing_parcel_keeps_custody_and_keeps_the_session_open() -> None:
    store = build_store()
    started = start_session(store)
    task = _accepted_task(store)
    hub_id = _list_and_arrive(store, task)
    handover = hub_handover_service(store)
    manifest_id = _active_manifest_id(store, task.shipment_id)
    handover.close_manifest(
        CloseManifestCommand(
            manifest_id=manifest_id,
            occurred_at=BASE_TIME + minutes(90),
            missing_shipment_ids=(task.shipment_id,),
        ),
        actor=hub_actor(hub_id),
    )
    item = store.handover_manifests.find_custody_item_for_shipment(task.shipment_id)
    assert item is not None
    assert item.status is HandoverManifestItemStatus.MISSING
    assert item.releases_custody is False

    service = work_session_service(store)
    with pytest.raises(DriverWorkSessionBlocked) as excinfo:
        service.end(
            EndWorkSessionCommand(
                driver_user_id=DRIVER_ID,
                session_id=started.session.session_id,
                reason="SESSION_COMPLETE",
                occurred_at=BASE_TIME + minutes(100),
            ),
            actor=driver_actor(),
        )
    assert WorkSessionBlocker.OPEN_PICKUP_CUSTODY.value in excinfo.value.blockers


def test_status_read_reports_offline_without_a_session() -> None:
    store = build_store()
    result = work_session_service(store).get_status(DRIVER_ID)
    assert result.session.availability is DriverAvailabilityStatus.OFFLINE
    assert result.session.status is WorkSessionStatus.ENDED


def test_history_records_actor_identity_for_every_transition() -> None:
    store = build_store()
    started = start_session(store)
    entries = store.task_history.list_entries_for_task(started.session.session_id)
    assert [entry.action for entry in entries] == ["work_session_started"]
    assert entries[0].actor_id == DRIVER_ID
    assert entries[0].actor_role == "PICKUP_DRIVER"


# --------------------------------------------------------------------- helpers


def _task_command(pickup_task_id):
    return TaskCommand(
        pickup_task_id=pickup_task_id,
        acting_driver_user_id=DRIVER_ID,
        occurred_at=BASE_TIME + minutes(1),
    )


def _accepted_task(store):
    task = register_task(
        store,
        status=PickupTaskStatus.PROOF_CAPTURED,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        has_condition_proof=True,
    )
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()
    return task


def _list_and_arrive(store, task):
    hub_id = uuid4()
    service = hub_handover_service(store)
    view = service.create_manifest(
        CreateHandoverManifestCommand(
            driver_user_id=DRIVER_ID,
            hub_id=hub_id,
            pickup_task_ids=(task.pickup_task_id,),
            occurred_at=BASE_TIME + minutes(70),
        ),
        actor=driver_actor(),
    )
    service.arrive_at_hub(
        ManifestLifecycleCommand(
            manifest_id=view.manifest.manifest_id,
            driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=driver_actor(),
    )
    return hub_id


def _active_manifest_id(store, shipment_id):
    item = store.handover_manifests.find_active_item_for_shipment(shipment_id)
    assert item is not None
    return item.manifest_id


def _release_at_hub(store, task):
    hub_id = _list_and_arrive(store, task)
    manifest_id = _active_manifest_id(store, task.shipment_id)
    hub_handover_service(store).record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(90),
        ),
        actor=hub_actor(hub_id),
    )
    return hub_id
