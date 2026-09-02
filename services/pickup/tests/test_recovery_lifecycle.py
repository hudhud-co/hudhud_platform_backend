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


def test_custody_started_shipment_cannot_be_recovered() -> None:
    service, _store, eligibility = _service()
    shipment_id = uuid4()
    task_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.IN_CUSTODY,
            custody_started=True,
            custody_type=CustodyType.DRIVER,
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

    with pytest.raises(CustodyAlreadyStarted):
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=task.pickup_task_id,
                idempotency_key="retry-custody",
                reason="should fail",
                occurred_at=_now(),
            )
        )


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
