"""Driver-to-hub handover manifests, custody release, and the outbox fact."""

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
    minutes,
    register_task,
)

from pickup.application.handover_fact_mapper import HandoverOutcome
from pickup.application.hub_handover_service import (
    CloseManifestCommand,
    CreateHandoverManifestCommand,
    ManifestLifecycleCommand,
    RecordHubReceiptCommand,
)
from pickup.domain.errors import (
    ActingDriverMismatch,
    HandoverManifestEmpty,
    HandoverManifestItemNotFound,
    HandoverManifestNotArrived,
    HandoverManifestNotFound,
    HandoverManifestNotMutable,
    HubScopeNotAuthorized,
    ShipmentAlreadyOnActiveManifest,
    ShipmentNotInDriverCustody,
)
from pickup.domain.handover import (
    HandoverDiscrepancyReason,
    HandoverManifestItemStatus,
    HandoverManifestStatus,
)
from pickup.domain.value_objects import (
    AssignmentState,
    EvidenceMediaRef,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
)

PHOTO = EvidenceMediaRef(
    ref_type="hub_condition_photo",
    bucket="hudhud-evidence",
    key="pickup/handover/condition-1.jpg",
    content_type="image/jpeg",
)


def _accepted_task(store, *, driver_user_id: str = DRIVER_ID):
    task = register_task(
        store,
        driver_user_id=driver_user_id,
        status=PickupTaskStatus.PROOF_CAPTURED,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        has_condition_proof=True,
    )
    task.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    task.version += 1
    store.begin()
    store.pickup_tasks.save_pickup_task(task)
    store.commit()
    return task


def _created(store, tasks, hub_id):
    return hub_handover_service(store).create_manifest(
        CreateHandoverManifestCommand(
            driver_user_id=DRIVER_ID,
            hub_id=hub_id,
            pickup_task_ids=tuple(task.pickup_task_id for task in tasks),
            occurred_at=BASE_TIME + minutes(60),
        ),
        actor=driver_actor(),
    )


def _arrived(store, manifest_id):
    return hub_handover_service(store).arrive_at_hub(
        ManifestLifecycleCommand(
            manifest_id=manifest_id,
            driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(70),
        ),
        actor=driver_actor(),
    )


# ------------------------------------------------------------------- creation


def test_manifest_lists_only_parcels_the_driver_actually_holds() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    view = _created(store, [task], hub_id)
    assert view.manifest.status is HandoverManifestStatus.READY
    assert view.manifest.expected_count == 1
    assert view.items[0].status is HandoverManifestItemStatus.EXPECTED
    assert view.manifest.manifest_code.startswith("PHM-")


def test_an_unaccepted_parcel_cannot_be_listed() -> None:
    store = build_store()
    task = register_task(store, assignment_state=AssignmentState.ACKNOWLEDGED)
    with pytest.raises(ShipmentNotInDriverCustody):
        _created(store, [task], uuid4())


def test_another_drivers_parcel_cannot_be_listed() -> None:
    store = build_store()
    task = _accepted_task(store, driver_user_id="driver-99")
    with pytest.raises(ActingDriverMismatch):
        _created(store, [task], uuid4())


def test_an_empty_manifest_is_rejected() -> None:
    store = build_store()
    with pytest.raises(HandoverManifestEmpty):
        _created(store, [], uuid4())


def test_a_parcel_cannot_sit_on_two_active_manifests() -> None:
    store = build_store()
    task = _accepted_task(store)
    _created(store, [task], uuid4())
    with pytest.raises(ShipmentAlreadyOnActiveManifest):
        _created(store, [task], uuid4())


def test_unknown_manifest_is_not_found() -> None:
    store = build_store()
    with pytest.raises(HandoverManifestNotFound):
        _arrived(store, uuid4())


# -------------------------------------------------------------------- arrival


def test_arrival_is_coordination_evidence_and_replays_safely() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    arrived = _arrived(store, created.manifest.manifest_id)
    assert arrived.manifest.status is HandoverManifestStatus.ARRIVED_AT_HUB
    again = _arrived(store, created.manifest.manifest_id)
    assert again.replayed is True


def test_receipt_before_arrival_is_refused() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    with pytest.raises(HandoverManifestNotArrived):
        hub_handover_service(store).record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=created.manifest.manifest_id,
                shipment_id=task.shipment_id,
                receiving_hub_id=hub_id,
                occurred_at=BASE_TIME + minutes(80),
            ),
            actor=hub_actor(hub_id),
        )


# -------------------------------------------------------------------- receipt


def test_clean_receipt_releases_custody_and_enqueues_the_fact() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    result = hub_handover_service(store).record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=hub_actor(hub_id),
    )
    assert result.custody_released is True
    assert result.item.status is HandoverManifestItemStatus.RECEIVED
    assert result.manifest.status is HandoverManifestStatus.COMPLETED
    assert result.outbox_record is not None
    payload = result.outbox_record.payload_json["payload"]
    assert result.outbox_record.event_type == "pickup.fact.handover_completed"
    assert payload["outcome"] == HandoverOutcome.RECEIVED.value
    assert payload["releasing_driver_user_id"] == DRIVER_ID
    assert payload["receiving_actor_id"] == "hub-operator-7"
    assert payload["shipment_id"] == str(task.shipment_id)


def test_the_fact_claims_a_pickup_task_version_never_a_shipment_version() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    result = hub_handover_service(store).record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=hub_actor(hub_id),
    )
    envelope = result.outbox_record.payload_json
    assert envelope["aggregate_type"] == "pickup_task"
    assert envelope["aggregate_id"] == str(task.pickup_task_id)
    assert envelope["aggregate_version"] == task.version + 1
    assert "shipment_aggregate_version" not in envelope["payload"]


def test_a_driver_may_not_record_its_own_hub_receipt() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    self_receipt = hub_actor(hub_id, actor_id=DRIVER_ID)
    with pytest.raises(HubScopeNotAuthorized):
        hub_handover_service(store).record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=created.manifest.manifest_id,
                shipment_id=task.shipment_id,
                receiving_hub_id=hub_id,
                occurred_at=BASE_TIME + minutes(80),
            ),
            actor=self_receipt,
        )


def test_an_unscoped_hub_operator_is_refused() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    with pytest.raises(HubScopeNotAuthorized):
        hub_handover_service(store).record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=created.manifest.manifest_id,
                shipment_id=task.shipment_id,
                receiving_hub_id=hub_id,
                occurred_at=BASE_TIME + minutes(80),
            ),
            actor=hub_actor(uuid4()),
        )


def test_a_concurrent_second_scan_is_a_replay_not_a_second_fact() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    service = hub_handover_service(store)
    command = RecordHubReceiptCommand(
        manifest_id=created.manifest.manifest_id,
        shipment_id=task.shipment_id,
        receiving_hub_id=hub_id,
        occurred_at=BASE_TIME + minutes(80),
    )
    first = service.record_hub_receipt(command, actor=hub_actor(hub_id))
    second = service.record_hub_receipt(command, actor=hub_actor(hub_id))
    assert second.replayed is True
    assert second.outbox_record is None
    assert len(store.outbox.list_for_aggregate(task.pickup_task_id)) == 1
    assert first.outbox_record is not None


def test_damage_at_handover_still_releases_custody_but_records_the_dispute() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    result = hub_handover_service(store).record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
            discrepancy_reason=HandoverDiscrepancyReason.DAMAGED_AT_HANDOVER,
            media_refs=(PHOTO,),
        ),
        actor=hub_actor(hub_id),
    )
    assert result.item.status is HandoverManifestItemStatus.DISCREPANCY
    assert result.item.releases_custody is True
    assert result.manifest.status is HandoverManifestStatus.COMPLETED_WITH_DISCREPANCIES
    payload = result.outbox_record.payload_json["payload"]
    assert payload["outcome"] == HandoverOutcome.RECEIVED_WITH_DISCREPANCY.value
    assert payload["discrepancy_reason"] == "DAMAGED_AT_HANDOVER"
    assert result.outbox_record.payload_json["media_refs"]


def test_a_dispute_receipt_without_evidence_is_refused() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    with pytest.raises(ValueError, match="media reference"):
        hub_handover_service(store).record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=created.manifest.manifest_id,
                shipment_id=task.shipment_id,
                receiving_hub_id=hub_id,
                occurred_at=BASE_TIME + minutes(80),
                discrepancy_reason=HandoverDiscrepancyReason.DAMAGED_AT_HANDOVER,
            ),
            actor=hub_actor(hub_id),
        )


def test_receiving_at_an_unplanned_hub_is_a_discrepancy_at_the_actual_hub() -> None:
    store = build_store()
    planned_hub = uuid4()
    actual_hub = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], planned_hub)
    _arrived(store, created.manifest.manifest_id)
    result = hub_handover_service(store).record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=actual_hub,
            occurred_at=BASE_TIME + minutes(80),
            media_refs=(PHOTO,),
        ),
        actor=hub_actor(actual_hub),
    )
    payload = result.outbox_record.payload_json["payload"]
    assert payload["receiving_hub_id"] == str(actual_hub)
    assert payload["discrepancy_reason"] == "WRONG_HUB_RECEIVED"
    assert result.item.releases_custody is True


def test_receipt_for_a_shipment_not_on_the_manifest_is_not_found() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    with pytest.raises(HandoverManifestItemNotFound):
        hub_handover_service(store).record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=created.manifest.manifest_id,
                shipment_id=uuid4(),
                receiving_hub_id=hub_id,
                occurred_at=BASE_TIME + minutes(80),
            ),
            actor=hub_actor(hub_id),
        )


# ---------------------------------------------------------------------- close


def test_closing_with_a_missing_parcel_keeps_it_with_the_driver() -> None:
    store = build_store()
    hub_id = uuid4()
    present = _accepted_task(store)
    missing = _accepted_task(store)
    created = _created(store, [present, missing], hub_id)
    _arrived(store, created.manifest.manifest_id)
    service = hub_handover_service(store)
    service.record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=present.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=hub_actor(hub_id),
    )
    closed = service.close_manifest(
        CloseManifestCommand(
            manifest_id=created.manifest.manifest_id,
            occurred_at=BASE_TIME + minutes(90),
            missing_shipment_ids=(missing.shipment_id,),
        ),
        actor=hub_actor(hub_id),
    )
    assert closed.manifest.status is HandoverManifestStatus.COMPLETED_WITH_DISCREPANCIES
    assert closed.manifest.missing_count == 1
    assert closed.manifest.received_count == 1
    # Exactly one custody-releasing fact: the missing parcel published nothing.
    facts = store.outbox.list_for_aggregate(present.pickup_task_id)
    assert len(facts) == 1
    assert store.outbox.list_for_aggregate(missing.pickup_task_id) == ()


def test_closing_must_name_every_unreceived_parcel() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    with pytest.raises(HandoverManifestItemNotFound):
        hub_handover_service(store).close_manifest(
            CloseManifestCommand(
                manifest_id=created.manifest.manifest_id,
                occurred_at=BASE_TIME + minutes(90),
            ),
            actor=hub_actor(hub_id),
        )


def test_closing_is_idempotent() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    service = hub_handover_service(store)
    service.record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=task.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=hub_actor(hub_id),
    )
    closed = service.close_manifest(
        CloseManifestCommand(
            manifest_id=created.manifest.manifest_id,
            occurred_at=BASE_TIME + minutes(90),
        ),
        actor=hub_actor(hub_id),
    )
    assert closed.replayed is True


def test_an_unscoped_actor_cannot_close_the_manifest() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    _arrived(store, created.manifest.manifest_id)
    with pytest.raises(HubScopeNotAuthorized):
        hub_handover_service(store).close_manifest(
            CloseManifestCommand(
                manifest_id=created.manifest.manifest_id,
                occurred_at=BASE_TIME + minutes(90),
            ),
            actor=hub_actor(uuid4()),
        )


# --------------------------------------------------------------- cancellation


def test_a_manifest_can_be_cancelled_before_any_hub_receipt() -> None:
    store = build_store()
    hub_id = uuid4()
    task = _accepted_task(store)
    created = _created(store, [task], hub_id)
    cancelled = hub_handover_service(store).cancel_manifest(
        ManifestLifecycleCommand(
            manifest_id=created.manifest.manifest_id,
            driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(65),
            reason="route changed",
        ),
        actor=driver_actor(),
    )
    assert cancelled.manifest.status is HandoverManifestStatus.CANCELLED


def test_a_manifest_cannot_be_cancelled_after_a_hub_took_custody() -> None:
    store = build_store()
    hub_id = uuid4()
    present = _accepted_task(store)
    other = _accepted_task(store)
    created = _created(store, [present, other], hub_id)
    _arrived(store, created.manifest.manifest_id)
    service = hub_handover_service(store)
    service.record_hub_receipt(
        RecordHubReceiptCommand(
            manifest_id=created.manifest.manifest_id,
            shipment_id=present.shipment_id,
            receiving_hub_id=hub_id,
            occurred_at=BASE_TIME + minutes(80),
        ),
        actor=hub_actor(hub_id),
    )
    with pytest.raises(HandoverManifestNotMutable):
        service.cancel_manifest(
            ManifestLifecycleCommand(
                manifest_id=created.manifest.manifest_id,
                driver_user_id=DRIVER_ID,
                occurred_at=BASE_TIME + minutes(85),
            ),
            actor=driver_actor(),
        )


def test_a_cancelled_manifest_releases_the_parcel_for_a_new_manifest() -> None:
    store = build_store()
    task = _accepted_task(store)
    created = _created(store, [task], uuid4())
    hub_handover_service(store).cancel_manifest(
        ManifestLifecycleCommand(
            manifest_id=created.manifest.manifest_id,
            driver_user_id=DRIVER_ID,
            occurred_at=BASE_TIME + minutes(65),
        ),
        actor=driver_actor(),
    )
    replacement = _created(store, [task], uuid4())
    assert replacement.manifest.manifest_id != created.manifest.manifest_id
