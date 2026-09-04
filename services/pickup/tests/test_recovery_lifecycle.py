"""Pickup recovery lifecycle invariant tests — Phase 12."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from pickup.application.recovery_service import (
    PickupRecoveryService,
    ReassignRecoveryCommand,
    RecoveryCommand,
    RegisterPickupTaskCommand,
    RescheduleRecoveryCommand,
)
from pickup.domain.entities import PickupTask
from pickup.domain.errors import (
    ConflictingIdempotencyKey,
    CustodyAlreadyStarted,
    InvalidRescheduleInput,
    MissingReassignmentDriver,
    PickupTaskAlreadyAccepted,
    PickupTaskNotRecoverable,
)
from pickup.domain.value_objects import (
    CustodyType,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    RecoveryAction,
    ScheduledWindow,
    ShipmentStatus,
)
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.memory import InMemoryRecoveryUnitOfWork, SimulatedCommitFailure
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot


def _now() -> datetime:
    return datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _window(*, hours_offset: int = 0) -> ScheduledWindow:
    start = _now() + timedelta(hours=hours_offset)
    return ScheduledWindow(start=start, end=start + timedelta(hours=2))


def _service() -> tuple[
    PickupRecoveryService,
    InMemoryRecoveryUnitOfWork,
    InMemoryShipmentEligibilityAdapter,
]:
    store = InMemoryRecoveryUnitOfWork()
    eligibility = InMemoryShipmentEligibilityAdapter()
    return PickupRecoveryService(store, eligibility), store, eligibility


def _seed_eligible_shipment(
    eligibility: InMemoryShipmentEligibilityAdapter,
    shipment_id: UUID,
) -> None:
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )


def _seed_recoverable_task(
    service: PickupRecoveryService,
    eligibility: InMemoryShipmentEligibilityAdapter,
    *,
    driver_id: str = "driver-42",
    window: ScheduledWindow | None = None,
) -> tuple[PickupTask, UUID]:
    shipment_id = uuid4()
    task_id = uuid4()
    batch_id = uuid4()
    _seed_eligible_shipment(eligibility, shipment_id)
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=batch_id,
            scheduled_window=window or _window(),
            created_at=_now(),
        )
    )
    return task, shipment_id


def test_retry_creates_exactly_one_incremented_replacement() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    occurred_at = _now() + timedelta(hours=1)

    result = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="retry-1",
            reason="driver unavailable",
            occurred_at=occurred_at,
        )
    )

    all_tasks = store.pickup_tasks.list_tasks_for_shipment(task.shipment_id)
    assert len(all_tasks) == 2
    assert result.original_task.status is PickupTaskStatus.SUPERSEDED
    assert result.replacement_task is not None
    assert result.replacement_task.attempt_number == 2
    assert result.replacement_task.status is PickupTaskStatus.PENDING
    assert result.replacement_task.assigned_driver_user_id == task.assigned_driver_user_id
    assert result.replacement_task.assigned_batch_id == task.assigned_batch_id
    assert "commit" in store.actions


def test_reschedule_preserves_history_and_changes_only_schedule() -> None:
    service, store, eligibility = _service()
    original_window = _window(hours_offset=0)
    task, _shipment_id = _seed_recoverable_task(service, eligibility, window=original_window)
    new_window = _window(hours_offset=24)
    occurred_at = _now() + timedelta(hours=1)

    result = service.reschedule_pickup(
        RescheduleRecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="reschedule-1",
            reason="merchant requested later slot",
            occurred_at=occurred_at,
            scheduled_window=new_window,
        )
    )

    original = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    replacement = result.replacement_task
    assert original is not None
    assert replacement is not None
    assert original.status is PickupTaskStatus.SUPERSEDED
    assert original.scheduled_window_start == original_window.start
    assert replacement.scheduled_window_start == new_window.start
    assert replacement.scheduled_window_end == new_window.end
    assert replacement.assigned_driver_user_id == task.assigned_driver_user_id
    assert replacement.assigned_batch_id == task.assigned_batch_id
    history = store.recovery_history.list_entries_for_task(task.pickup_task_id)
    assert len(history) == 1
    assert history[0].action is RecoveryAction.RESCHEDULE


def test_reassign_requires_and_applies_new_driver() -> None:
    service, _store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility, driver_id="driver-old")

    with pytest.raises(MissingReassignmentDriver):
        service.reassign_pickup(
            ReassignRecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="reassign-missing",
                reason="coverage gap",
                occurred_at=_now(),
                new_driver_user_id=None,
            )
        )

    result = service.reassign_pickup(
        ReassignRecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="reassign-1",
            reason="coverage gap",
            occurred_at=_now() + timedelta(minutes=5),
            new_driver_user_id="driver-new",
        )
    )

    assert result.replacement_task is not None
    assert result.replacement_task.assigned_driver_user_id == "driver-new"
    assert result.replacement_task.assigned_batch_id == task.assigned_batch_id
    assert result.replacement_task.scheduled_window_start == task.scheduled_window_start


def test_cancel_preserves_task_without_replacement() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    occurred_at = _now() + timedelta(minutes=10)

    result = service.cancel_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="cancel-1",
            reason="merchant cancelled order",
            occurred_at=occurred_at,
        )
    )

    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored is not None
    assert result.replacement_task is None
    assert stored.status is PickupTaskStatus.CANCELLED
    assert stored.cancelled_at == occurred_at
    assert len(store.pickup_tasks.list_tasks_for_shipment(task.shipment_id)) == 1


def test_previous_task_links_to_replacement_and_lineage() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)

    result = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="retry-lineage",
            reason="retry",
            occurred_at=_now(),
        )
    )

    original = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    replacement = result.replacement_task
    assert original is not None
    assert replacement is not None
    assert original.superseded_by_task_id == replacement.pickup_task_id
    assert replacement.parent_attempt_id == original.pickup_task_id
    assert replacement.root_attempt_id == original.root_attempt_id == task.pickup_task_id


def test_accepted_task_cannot_be_recovered() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    accepted = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert accepted is not None
    accepted.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.pickup_tasks.save_pickup_task(accepted)

    with pytest.raises(PickupTaskAlreadyAccepted):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="retry-accepted",
                reason="should fail",
                occurred_at=_now(),
            )
        )


def test_pickup_driver_custody_blocks_recovery_without_mutation() -> None:
    service, store, eligibility = _service()
    shipment_id = uuid4()
    task_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.IN_CUSTODY,
            custody_started=True,
            custody_type=CustodyType.PICKUP_DRIVER,
            custody_id="driver-42",
        )
    )
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            created_at=_now(),
        )
    )
    before = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert before is not None
    before_status = before.status
    before_version = before.version
    snapshot_before = eligibility.get_eligibility(shipment_id)

    with pytest.raises(CustodyAlreadyStarted):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="retry-pickup-driver",
                reason="should fail",
                occurred_at=_now(),
            )
        )

    after = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert after is not None
    assert after.status is before_status
    assert after.version == before_version
    assert after.superseded_by_task_id is None
    assert len(store.pickup_tasks.list_tasks_for_shipment(shipment_id)) == 1
    assert not store.recovery_history.list_entries_for_task(task.pickup_task_id)
    assert store.idempotency.get_record("retry-pickup-driver") is None
    assert eligibility.get_eligibility(shipment_id) == snapshot_before


@pytest.mark.parametrize(
    ("shipment_status", "custody_started", "custody_id"),
    [
        (ShipmentStatus.IN_CUSTODY, True, "cust-1"),
        (ShipmentStatus.IN_CUSTODY, False, None),
        (ShipmentStatus.CREATED, True, "cust-2"),
        (ShipmentStatus.CREATED, False, "cust-3"),
    ],
)
def test_non_pickup_driver_snapshot_fields_do_not_block_recovery(
    shipment_status: ShipmentStatus,
    custody_started: bool,
    custody_id: str | None,
) -> None:
    """IN_CUSTODY, custody_started, and custody_id alone are not authorization inputs."""
    service, store, eligibility = _service()
    shipment_id = uuid4()
    task_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=shipment_status,
            custody_started=custody_started,
            custody_type=None,
            custody_id=custody_id,
        )
    )
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            created_at=_now(),
        )
    )

    result = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key=f"retry-allow-{shipment_status.value}-{custody_started}-{custody_id}",
            reason="allowed",
            occurred_at=_now(),
        )
    )

    assert result.replacement_task is not None
    assert result.original_task.status is PickupTaskStatus.SUPERSEDED
    assert len(store.pickup_tasks.list_tasks_for_shipment(shipment_id)) == 2


def test_missing_shipment_eligibility_fails_closed() -> None:
    service, store, eligibility = _service()
    task_id = uuid4()
    shipment_id = uuid4()
    # Intentionally do not seed eligibility — shipment evidence unavailable.
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            created_at=_now(),
        )
    )

    with pytest.raises(CustodyAlreadyStarted) as exc_info:
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="retry-missing-eligibility",
                reason="should fail closed",
                occurred_at=_now(),
            )
        )

    assert exc_info.value.shipment_status == "UNKNOWN"
    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored is not None
    assert stored.status is PickupTaskStatus.PROOF_CAPTURED
    assert store.idempotency.get_record("retry-missing-eligibility") is None


def test_pickup_driver_custody_blocks_all_recovery_actions() -> None:
    service, store, eligibility = _service()
    shipment_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=CustodyType.PICKUP_DRIVER,
            custody_id=None,
        )
    )

    def _register() -> PickupTask:
        return service.register_pickup_task(
            RegisterPickupTaskCommand(
                pickup_task_id=uuid4(),
                shipment_id=shipment_id,
                assigned_driver_user_id="driver-42",
                assigned_batch_id=uuid4(),
                scheduled_window=_window(),
                created_at=_now(),
            )
        )

    retry_task = _register()
    with pytest.raises(CustodyAlreadyStarted):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=retry_task.pickup_task_id,
                idempotency_key="block-retry",
                reason="blocked",
                occurred_at=_now(),
            )
        )

    reschedule_task = _register()
    with pytest.raises(CustodyAlreadyStarted):
        service.reschedule_pickup(
            RescheduleRecoveryCommand(
                pickup_task_id=reschedule_task.pickup_task_id,
                idempotency_key="block-reschedule",
                reason="blocked",
                occurred_at=_now(),
                scheduled_window=_window(hours_offset=24),
            )
        )

    reassign_task = _register()
    with pytest.raises(CustodyAlreadyStarted):
        service.reassign_pickup(
            ReassignRecoveryCommand(
                pickup_task_id=reassign_task.pickup_task_id,
                idempotency_key="block-reassign",
                reason="blocked",
                occurred_at=_now(),
                new_driver_user_id="driver-new",
            )
        )

    cancel_task = _register()
    with pytest.raises(CustodyAlreadyStarted):
        service.cancel_pickup(
            RecoveryCommand(
                pickup_task_id=cancel_task.pickup_task_id,
                idempotency_key="block-cancel",
                reason="blocked",
                occurred_at=_now(),
            )
        )

    for task in (retry_task, reschedule_task, reassign_task, cancel_task):
        stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
        assert stored is not None
        assert stored.status is PickupTaskStatus.PROOF_CAPTURED
        assert stored.superseded_by_task_id is None
        assert not store.recovery_history.list_entries_for_task(task.pickup_task_id)


def test_recovery_never_changes_shipment_snapshot() -> None:
    service, _store, eligibility = _service()
    task, shipment_id = _seed_recoverable_task(service, eligibility)
    before = eligibility.get_eligibility(shipment_id)
    assert before is not None

    service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="retry-no-shipment-mutation",
            reason="retry",
            occurred_at=_now(),
        )
    )

    after = eligibility.get_eligibility(shipment_id)
    assert after == before


def test_repeated_idempotency_key_returns_same_result() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    command = RecoveryCommand(
        pickup_task_id=task.pickup_task_id,
        idempotency_key="idem-retry",
        reason="retry",
        occurred_at=_now(),
    )

    first = service.retry_pickup(command)
    second = service.retry_pickup(command)

    assert second.idempotent_replay is True
    assert second.replacement_task is not None
    assert first.replacement_task is not None
    assert second.replacement_task.pickup_task_id == first.replacement_task.pickup_task_id
    assert len(store.pickup_tasks.list_tasks_for_shipment(task.shipment_id)) == 2
    assert len(store.recovery_history.list_entries_for_task(task.pickup_task_id)) == 1


def test_conflicting_idempotency_key_fails() -> None:
    service, _store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="shared-key",
            reason="retry",
            occurred_at=_now(),
        )
    )

    with pytest.raises(ConflictingIdempotencyKey):
        service.cancel_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="shared-key",
                reason="cancel",
                occurred_at=_now(),
            )
        )


def test_recovery_of_superseded_or_cancelled_task_fails() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    service.cancel_pickup(
        RecoveryCommand(
            pickup_task_id=task.pickup_task_id,
            idempotency_key="cancel-first",
            reason="cancel",
            occurred_at=_now(),
        )
    )

    with pytest.raises(PickupTaskNotRecoverable):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="retry-after-cancel",
                reason="retry",
                occurred_at=_now(),
            )
        )

    task2, _ = _seed_recoverable_task(service, eligibility)
    retry_result = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=task2.pickup_task_id,
            idempotency_key="retry-supersede",
            reason="retry",
            occurred_at=_now(),
        )
    )
    assert retry_result.replacement_task is not None
    with pytest.raises(PickupTaskNotRecoverable):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task2.pickup_task_id,
                idempotency_key="retry-after-supersede",
                reason="retry again",
                occurred_at=_now(),
            )
        )

    assert len(store.pickup_tasks.list_tasks_for_shipment(task2.shipment_id)) == 2


def test_rollback_leaves_task_history_and_idempotency_unchanged() -> None:
    service, store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    store.fail_on_commit = True

    with pytest.raises(SimulatedCommitFailure):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="rollback-retry",
                reason="retry",
                occurred_at=_now(),
            )
        )

    stored = store.pickup_tasks.get_pickup_task(task.pickup_task_id)
    assert stored is not None
    assert stored.status is PickupTaskStatus.PROOF_CAPTURED
    assert stored.superseded_by_task_id is None
    assert len(store.pickup_tasks.list_tasks_for_shipment(task.shipment_id)) == 1
    assert not store.recovery_history.list_entries_for_task(task.pickup_task_id)
    assert store.idempotency.get_record("rollback-retry") is None
    assert "rollback" in store.actions


def test_invalid_reschedule_window_rejected() -> None:
    service, _store, eligibility = _service()
    task, _shipment_id = _seed_recoverable_task(service, eligibility)
    start = _now()
    bad_window = ScheduledWindow(start=start, end=start)

    with pytest.raises(InvalidRescheduleInput):
        service.reschedule_pickup(
            RescheduleRecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="bad-window",
                reason="bad",
                occurred_at=_now(),
                scheduled_window=bad_window,
            )
        )

    with pytest.raises(InvalidRescheduleInput):
        service.reschedule_pickup(
            RescheduleRecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="missing-window",
                reason="bad",
                occurred_at=_now(),
                scheduled_window=None,
            )
        )
