"""In-memory Pickup persistence with rollback-safe unit of work."""

from __future__ import annotations

import copy
import threading
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from pickup.domain.entities import (
    AcceptanceIdempotencyRecord,
    IdempotencyRecord,
    OutboxRecord,
    PickupTask,
    RecoveryHistoryEntry,
)
from pickup.domain.value_objects import OutboxStatus


class SimulatedCommitFailure(RuntimeError):
    """Test hook: forces rollback before commit completes."""


class InMemoryPickupUnitOfWork:
    """Rollback-safe in-memory UoW for recovery and acceptance unit tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pickup_tasks: dict[UUID, PickupTask] = {}
        self._recovery_history: list[RecoveryHistoryEntry] = []
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._acceptance_idempotency: dict[str, AcceptanceIdempotencyRecord] = {}
        self._outbox: dict[UUID, OutboxRecord] = {}
        self._outbox_by_event_id: dict[UUID, UUID] = {}
        self._tx_pickup_tasks: dict[UUID, PickupTask] | None = None
        self._tx_recovery_history: list[RecoveryHistoryEntry] | None = None
        self._tx_idempotency: dict[str, IdempotencyRecord] | None = None
        self._tx_acceptance_idempotency: dict[str, AcceptanceIdempotencyRecord] | None = None
        self._tx_outbox: dict[UUID, OutboxRecord] | None = None
        self._tx_outbox_by_event_id: dict[UUID, UUID] | None = None
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

    @property
    def acceptance_idempotency(self) -> _AcceptanceIdempotencyRepo:
        return _AcceptanceIdempotencyRepo(self)

    @property
    def outbox(self) -> _OutboxRepo:
        return _OutboxRepo(self)

    def recover_stale_processing(self, *, now: datetime) -> int:
        return self.outbox.recover_stale_processing(now=now)

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        return self.outbox.claim_batch(
            owner=owner,
            batch_size=batch_size,
            lease_until=lease_until,
            now=now,
        )

    def apply_publish_decision(
        self,
        *,
        outbox_id: UUID,
        status: str,
        clear_owner: bool,
        clear_lease: bool,
        published_at: datetime | None,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> None:
        self.outbox.apply_publish_decision(
            outbox_id=outbox_id,
            status=status,
            clear_owner=clear_owner,
            clear_lease=clear_lease,
            published_at=published_at,
            next_attempt_at=next_attempt_at,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
        )

    def begin(self) -> None:
        self._lock.acquire()
        self._tx_pickup_tasks = copy.deepcopy(self._pickup_tasks)
        self._tx_recovery_history = copy.deepcopy(self._recovery_history)
        self._tx_idempotency = copy.deepcopy(self._idempotency)
        self._tx_acceptance_idempotency = copy.deepcopy(self._acceptance_idempotency)
        self._tx_outbox = copy.deepcopy(self._outbox)
        self._tx_outbox_by_event_id = copy.deepcopy(self._outbox_by_event_id)
        self.actions.append("begin")

    def commit(self) -> None:
        if self._tx_pickup_tasks is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        try:
            if self.fail_on_commit:
                self._clear_tx_buffers()
                self.actions.append("rollback")
                raise SimulatedCommitFailure("simulated commit failure")
            self._pickup_tasks = self._tx_pickup_tasks
            self._recovery_history = self._tx_recovery_history  # type: ignore[assignment]
            self._idempotency = self._tx_idempotency  # type: ignore[assignment]
            self._acceptance_idempotency = self._tx_acceptance_idempotency  # type: ignore[assignment]
            self._outbox = self._tx_outbox  # type: ignore[assignment]
            self._outbox_by_event_id = self._tx_outbox_by_event_id  # type: ignore[assignment]
            self._clear_tx_buffers()
            self.actions.append("commit")
        finally:
            self._lock.release()

    def rollback(self) -> None:
        had_open_tx = self._tx_pickup_tasks is not None
        self._clear_tx_buffers()
        self.actions.append("rollback")
        if had_open_tx:
            self._lock.release()

    def _clear_tx_buffers(self) -> None:
        self._tx_pickup_tasks = None
        self._tx_recovery_history = None
        self._tx_idempotency = None
        self._tx_acceptance_idempotency = None
        self._tx_outbox = None
        self._tx_outbox_by_event_id = None

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

    def _working_acceptance_idempotency(self) -> dict[str, AcceptanceIdempotencyRecord]:
        if self._tx_acceptance_idempotency is not None:
            return self._tx_acceptance_idempotency
        return self._acceptance_idempotency

    def _working_outbox(self) -> dict[UUID, OutboxRecord]:
        if self._tx_outbox is not None:
            return self._tx_outbox
        return self._outbox

    def _working_outbox_by_event_id(self) -> dict[UUID, UUID]:
        if self._tx_outbox_by_event_id is not None:
            return self._tx_outbox_by_event_id
        return self._outbox_by_event_id


# Backward-compatible alias for recovery tests.
InMemoryRecoveryUnitOfWork = InMemoryPickupUnitOfWork


class _PickupTaskRepo:
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
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
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
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
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
        self._store = store

    def save_record(self, record: IdempotencyRecord) -> None:
        self._store._working_idempotency()[record.idempotency_key] = copy.deepcopy(record)

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        record = self._store._working_idempotency().get(idempotency_key)
        return copy.deepcopy(record) if record is not None else None


class _AcceptanceIdempotencyRepo:
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
        self._store = store

    def save_record(self, record: AcceptanceIdempotencyRecord) -> None:
        self._store._working_acceptance_idempotency()[record.idempotency_key] = copy.deepcopy(
            record
        )

    def get_record(self, idempotency_key: str) -> AcceptanceIdempotencyRecord | None:
        record = self._store._working_acceptance_idempotency().get(idempotency_key)
        return copy.deepcopy(record) if record is not None else None


class _OutboxRepo:
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
        self._store = store

    def insert(self, record: OutboxRecord) -> None:
        outbox = self._store._working_outbox()
        by_event = self._store._working_outbox_by_event_id()
        if record.event_id in by_event:
            msg = f"duplicate outbox event_id: {record.event_id}"
            raise ValueError(msg)
        for existing in outbox.values():
            if (
                existing.aggregate_id == record.aggregate_id
                and existing.aggregate_version == record.aggregate_version
            ):
                msg = (
                    f"duplicate outbox aggregate version: "
                    f"{record.aggregate_id}@{record.aggregate_version}"
                )
                raise ValueError(msg)
            if (
                existing.aggregate_id == record.aggregate_id
                and existing.event_type == record.event_type
            ):
                msg = (
                    f"duplicate acceptance fact for aggregate: "
                    f"{record.aggregate_id} event_type={record.event_type}"
                )
                raise ValueError(msg)
        outbox[record.id] = copy.deepcopy(record)
        by_event[record.event_id] = record.id

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        row_id = self._store._working_outbox_by_event_id().get(event_id)
        if row_id is None:
            return None
        record = self._store._working_outbox().get(row_id)
        return copy.deepcopy(record) if record is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working_outbox().values()
            if record.status is OutboxStatus.PENDING
        )

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working_outbox().values()
            if record.aggregate_id == aggregate_id
        )

    def recover_stale_processing(self, *, now: datetime) -> int:
        recovered = 0
        outbox = self._store._outbox
        for row_id, row in list(outbox.items()):
            if row.status is not OutboxStatus.PROCESSING:
                continue
            if row.processing_until and row.processing_until < now:
                outbox[row_id] = replace(
                    row,
                    status=OutboxStatus.PENDING,
                    processing_owner=None,
                    processing_until=None,
                    next_attempt_at=now,
                    last_error_code="STALE_LEASE",
                    last_error_message="stale_processing_lease",
                )
                recovered += 1
        return recovered

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        outbox = self._store._outbox
        claimable = [
            row
            for row in outbox.values()
            if row.status is OutboxStatus.PENDING and row.next_attempt_at <= now
        ]
        claimable.sort(key=lambda row: row.next_attempt_at)
        claimed: list[OutboxRecord] = []
        for row in claimable[:batch_size]:
            updated = replace(
                row,
                status=OutboxStatus.PROCESSING,
                attempt_count=row.attempt_count + 1,
                processing_owner=owner,
                processing_until=lease_until,
            )
            outbox[row.id] = updated
            claimed.append(copy.deepcopy(updated))
        return claimed

    def apply_publish_decision(
        self,
        *,
        outbox_id: UUID,
        status: str,
        clear_owner: bool,
        clear_lease: bool,
        published_at: datetime | None,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> None:
        outbox = self._store._outbox
        row = outbox[outbox_id]
        outbox[outbox_id] = replace(
            row,
            status=OutboxStatus(status),
            next_attempt_at=next_attempt_at or row.next_attempt_at,
            processing_owner=None if clear_owner else row.processing_owner,
            processing_until=None if clear_lease else row.processing_until,
            published_at=published_at or row.published_at,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
        )
