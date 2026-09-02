"""In-memory recovery persistence with rollback-safe unit of work."""

from __future__ import annotations

import copy
from uuid import UUID

from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry


class SimulatedCommitFailure(RuntimeError):
    """Test hook: forces rollback before commit completes."""


class InMemoryRecoveryUnitOfWork:
    """Rollback-safe in-memory UoW for W12 recovery unit tests only."""

    def __init__(self) -> None:
        self._pickup_tasks: dict[UUID, PickupTask] = {}
        self._recovery_history: list[RecoveryHistoryEntry] = []
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._tx_pickup_tasks: dict[UUID, PickupTask] | None = None
        self._tx_recovery_history: list[RecoveryHistoryEntry] | None = None
        self._tx_idempotency: dict[str, IdempotencyRecord] | None = None
        self.fail_on_commit = False
        self.actions: list[str] = []

    @property
    def pickup_tasks(self) -> _PickupTaskRepo:
        return _PickupTaskRepo(self)

    @property
    def recovery_history(self) -> _RecoveryHistoryRepo:
        return _RecoveryHistoryRepo(self)

    @property
    def idempotency(self) -> _IdempotencyRepo:
        return _IdempotencyRepo(self)

    def begin(self) -> None:
        self._tx_pickup_tasks = copy.deepcopy(self._pickup_tasks)
        self._tx_recovery_history = copy.deepcopy(self._recovery_history)
        self._tx_idempotency = copy.deepcopy(self._idempotency)
        self.actions.append("begin")

    def commit(self) -> None:
        if self._tx_pickup_tasks is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        if self.fail_on_commit:
            self.rollback()
            raise SimulatedCommitFailure("simulated commit failure")
        self._pickup_tasks = self._tx_pickup_tasks
        self._recovery_history = self._tx_recovery_history
        self._idempotency = self._tx_idempotency
        self._tx_pickup_tasks = None
        self._tx_recovery_history = None
        self._tx_idempotency = None
        self.actions.append("commit")

    def rollback(self) -> None:
        self._tx_pickup_tasks = None
        self._tx_recovery_history = None
        self._tx_idempotency = None
        self.actions.append("rollback")

    def _working_pickup_tasks(self) -> dict[UUID, PickupTask]:
        if self._tx_pickup_tasks is not None:
            return self._tx_pickup_tasks
        return self._pickup_tasks

    def _working_recovery_history(self) -> list[RecoveryHistoryEntry]:
        if self._tx_recovery_history is not None:
            return self._tx_recovery_history
        return self._recovery_history

    def _working_idempotency(self) -> dict[str, IdempotencyRecord]:
        if self._tx_idempotency is not None:
            return self._tx_idempotency
        return self._idempotency


class _PickupTaskRepo:
    def __init__(self, store: InMemoryRecoveryUnitOfWork) -> None:
        self._store = store

    def save_pickup_task(self, pickup_task: PickupTask) -> None:
        self._store._working_pickup_tasks()[pickup_task.pickup_task_id] = copy.deepcopy(
            pickup_task
        )

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTask | None:
        task = self._store._working_pickup_tasks().get(pickup_task_id)
        return copy.deepcopy(task) if task is not None else None

    def list_tasks_for_shipment(self, shipment_id: UUID) -> tuple[PickupTask, ...]:
        return tuple(
            copy.deepcopy(task)
            for task in self._store._working_pickup_tasks().values()
            if task.shipment_id == shipment_id
        )


class _RecoveryHistoryRepo:
    def __init__(self, store: InMemoryRecoveryUnitOfWork) -> None:
        self._store = store

    def append_entry(self, entry: RecoveryHistoryEntry) -> None:
        self._store._working_recovery_history().append(copy.deepcopy(entry))

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[RecoveryHistoryEntry, ...]:
        return tuple(
            copy.deepcopy(entry)
            for entry in self._store._working_recovery_history()
            if entry.pickup_task_id == pickup_task_id
        )


class _IdempotencyRepo:
    def __init__(self, store: InMemoryRecoveryUnitOfWork) -> None:
        self._store = store

    def save_record(self, record: IdempotencyRecord) -> None:
        self._store._working_idempotency()[record.idempotency_key] = copy.deepcopy(record)

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        record = self._store._working_idempotency().get(idempotency_key)
        return copy.deepcopy(record) if record is not None else None
