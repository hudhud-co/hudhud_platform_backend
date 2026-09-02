"""PostgreSQL recovery unit-of-work adapter with optimistic concurrency."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry
from pickup.domain.errors import StalePickupTaskVersion
from pickup.domain.value_objects import PickupTaskAcceptanceState, PickupTaskStatus, RecoveryAction
from pickup.infrastructure.persistence.models import (
    PickupTaskRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
)


@dataclass
class SqlAlchemyRecoveryUnitOfWork:
    """Transactional recovery boundary — one PostgreSQL transaction for all effects."""

    session_factory: sessionmaker[Session]
    _session: Session | None = None
    _pending_tasks: dict[UUID, tuple[PickupTask, int | None]] | None = None
    _pending_history: list[RecoveryHistoryEntry] | None = None
    _pending_idempotency: dict[str, IdempotencyRecord] | None = None

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
        self._session = self.session_factory()
        self._pending_tasks = {}
        self._pending_history = []
        self._pending_idempotency = {}

    def commit(self) -> None:
        if self._session is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        assert self._pending_tasks is not None
        assert self._pending_history is not None
        assert self._pending_idempotency is not None

        for entity, previous_version in self._pending_tasks.values():
            if previous_version is None:
                session.add(_task_to_row(entity))
                continue
            rowcount = session.execute(
                update(PickupTaskRow)
                .where(
                    PickupTaskRow.pickup_task_id == entity.pickup_task_id,
                    PickupTaskRow.version == previous_version,
                )
                .values(**_task_update_values(entity))
            ).rowcount
            if rowcount == 0:
                raise StalePickupTaskVersion(
                    pickup_task_id=str(entity.pickup_task_id),
                    expected_version=previous_version,
                    actual_version=entity.version,
                )

        for entry in self._pending_history:
            session.add(_history_to_row(entry))

        for record in self._pending_idempotency.values():
            session.add(_idempotency_to_row(record))

        session.commit()
        self._session.close()
        self._session = None
        self._pending_tasks = None
        self._pending_history = None
        self._pending_idempotency = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending_tasks = None
        self._pending_history = None
        self._pending_idempotency = None

    def _in_transaction(self) -> bool:
        return self._session is not None

    def _working_tasks(self) -> dict[UUID, tuple[PickupTask, int | None]]:
        if self._pending_tasks is None:
            msg = "pickup task mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_tasks

    def _working_history(self) -> list[RecoveryHistoryEntry]:
        if self._pending_history is None:
            msg = "recovery history mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_history

    def _working_idempotency(self) -> dict[str, IdempotencyRecord]:
        if self._pending_idempotency is None:
            msg = "idempotency mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_idempotency

    def _load_task_version(self, pickup_task_id: UUID) -> int | None:
        pending = self._pending_tasks
        if pending is not None and pickup_task_id in pending:
            return pending[pickup_task_id][0].version
        if self._session is not None:
            row = self._session.get(PickupTaskRow, pickup_task_id)
            return row.version if row is not None else None  # type: ignore[return-value]
        with self.session_factory() as session:
            row = session.get(PickupTaskRow, pickup_task_id)
            return row.version if row is not None else None  # type: ignore[return-value]

    def _persist_task_immediately(self, pickup_task: PickupTask) -> None:
        previous_version = self._load_task_version(pickup_task.pickup_task_id)
        with self.session_factory() as session:
            if previous_version is None:
                session.add(_task_to_row(pickup_task))
            else:
                rowcount = session.execute(
                    update(PickupTaskRow)
                    .where(
                        PickupTaskRow.pickup_task_id == pickup_task.pickup_task_id,
                        PickupTaskRow.version == previous_version,
                    )
                    .values(**_task_update_values(pickup_task))
                ).rowcount
                if rowcount == 0:
                    raise StalePickupTaskVersion(
                        pickup_task_id=str(pickup_task.pickup_task_id),
                        expected_version=previous_version,
                        actual_version=pickup_task.version,
                    )
            session.commit()


class _PickupTaskRepo:
    def __init__(self, store: SqlAlchemyRecoveryUnitOfWork) -> None:
        self._store = store

    def save_pickup_task(self, pickup_task: PickupTask) -> None:
        previous_version = self._store._load_task_version(pickup_task.pickup_task_id)
        if self._store._in_transaction():
            self._store._working_tasks()[pickup_task.pickup_task_id] = (
                pickup_task,
                previous_version,
            )
            return
        self._store._persist_task_immediately(pickup_task)

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTask | None:
        pending = self._store._pending_tasks
        if pending is not None and pickup_task_id in pending:
            return _copy_task(pending[pickup_task_id][0])
        if self._store._session is not None:
            row = self._store._session.get(PickupTaskRow, pickup_task_id)
            return _task_from_row(row) if row is not None else None
        with self._store.session_factory() as session:
            row = session.get(PickupTaskRow, pickup_task_id)
            return _task_from_row(row) if row is not None else None

    def list_tasks_for_shipment(self, shipment_id: UUID) -> tuple[PickupTask, ...]:
        tasks: dict[UUID, PickupTask] = {}
        if self._store._session is not None:
            rows = self._store._session.execute(
                select(PickupTaskRow).where(PickupTaskRow.shipment_id == shipment_id)
            ).scalars()
            for row in rows:
                tasks[row.pickup_task_id] = _task_from_row(row)  # type: ignore[index]
            pending = self._store._pending_tasks
            if pending is not None:
                for entity, _previous in pending.values():
                    if entity.shipment_id == shipment_id:
                        tasks[entity.pickup_task_id] = _copy_task(entity)
        else:
            with self._store.session_factory() as session:
                rows = session.execute(
                    select(PickupTaskRow).where(PickupTaskRow.shipment_id == shipment_id)
                ).scalars()
                for row in rows:
                    tasks[row.pickup_task_id] = _task_from_row(row)  # type: ignore[index]
        return tuple(tasks.values())


class _RecoveryHistoryRepo:
    def __init__(self, store: SqlAlchemyRecoveryUnitOfWork) -> None:
        self._store = store

    def append_entry(self, entry: RecoveryHistoryEntry) -> None:
        if self._store._in_transaction():
            self._store._working_history().append(entry)
            return
        msg = "recovery history append outside transaction"
        raise RuntimeError(msg)

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[RecoveryHistoryEntry, ...]:
        entries: list[RecoveryHistoryEntry] = []
        if self._store._session is not None:
            rows = self._store._session.execute(
                select(RecoveryHistoryRow).where(
                    RecoveryHistoryRow.pickup_task_id == pickup_task_id
                )
            ).scalars()
            entries.extend(_history_from_row(row) for row in rows)
            pending = self._store._pending_history
            if pending is not None:
                entries.extend(
                    entry for entry in pending if entry.pickup_task_id == pickup_task_id
                )
        else:
            with self._store.session_factory() as session:
                rows = session.execute(
                    select(RecoveryHistoryRow).where(
                        RecoveryHistoryRow.pickup_task_id == pickup_task_id
                    )
                ).scalars()
                entries.extend(_history_from_row(row) for row in rows)
        return tuple(entries)


class _IdempotencyRepo:
    def __init__(self, store: SqlAlchemyRecoveryUnitOfWork) -> None:
        self._store = store

    def save_record(self, record: IdempotencyRecord) -> None:
        if self._store._in_transaction():
            self._store._working_idempotency()[record.idempotency_key] = record
            return
        msg = "idempotency save outside transaction"
        raise RuntimeError(msg)

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        pending = self._store._pending_idempotency
        if pending is not None and idempotency_key in pending:
            return pending[idempotency_key]
        if self._store._session is not None:
            row = self._store._session.get(RecoveryIdempotencyRow, idempotency_key)
            return _idempotency_from_row(row) if row is not None else None
        with self._store.session_factory() as session:
            row = session.get(RecoveryIdempotencyRow, idempotency_key)
            return _idempotency_from_row(row) if row is not None else None


def _copy_task(task: PickupTask) -> PickupTask:
    return PickupTask(
        pickup_task_id=task.pickup_task_id,
        shipment_id=task.shipment_id,
        assigned_driver_user_id=task.assigned_driver_user_id,
        assigned_batch_id=task.assigned_batch_id,
        status=task.status,
        attempt_number=task.attempt_number,
        root_attempt_id=task.root_attempt_id,
        parent_attempt_id=task.parent_attempt_id,
        superseded_by_task_id=task.superseded_by_task_id,
        scheduled_window_start=task.scheduled_window_start,
        scheduled_window_end=task.scheduled_window_end,
        acceptance_state=task.acceptance_state,
        recovery_reason=task.recovery_reason,
        created_at=task.created_at,
        recovered_at=task.recovered_at,
        cancelled_at=task.cancelled_at,
        version=task.version,
    )


def _task_to_row(task: PickupTask) -> PickupTaskRow:
    return PickupTaskRow(
        pickup_task_id=task.pickup_task_id,
        shipment_id=task.shipment_id,
        assigned_driver_user_id=task.assigned_driver_user_id,
        assigned_batch_id=task.assigned_batch_id,
        status=task.status.value,
        attempt_number=task.attempt_number,
        root_attempt_id=task.root_attempt_id,
        parent_attempt_id=task.parent_attempt_id,
        superseded_by_task_id=task.superseded_by_task_id,
        scheduled_window_start=task.scheduled_window_start,
        scheduled_window_end=task.scheduled_window_end,
        acceptance_state=task.acceptance_state.value if task.acceptance_state else None,
        recovery_reason=task.recovery_reason,
        created_at=task.created_at,
        recovered_at=task.recovered_at,
        cancelled_at=task.cancelled_at,
        version=task.version,
    )


def _task_update_values(task: PickupTask) -> dict[str, object]:
    return {
        "shipment_id": task.shipment_id,
        "assigned_driver_user_id": task.assigned_driver_user_id,
        "assigned_batch_id": task.assigned_batch_id,
        "status": task.status.value,
        "attempt_number": task.attempt_number,
        "root_attempt_id": task.root_attempt_id,
        "parent_attempt_id": task.parent_attempt_id,
        "superseded_by_task_id": task.superseded_by_task_id,
        "scheduled_window_start": task.scheduled_window_start,
        "scheduled_window_end": task.scheduled_window_end,
        "acceptance_state": task.acceptance_state.value if task.acceptance_state else None,
        "recovery_reason": task.recovery_reason,
        "created_at": task.created_at,
        "recovered_at": task.recovered_at,
        "cancelled_at": task.cancelled_at,
        "version": task.version,
    }


def _task_from_row(row: PickupTaskRow) -> PickupTask:
    acceptance: PickupTaskAcceptanceState | None = None
    if row.acceptance_state is not None:
        acceptance = PickupTaskAcceptanceState(row.acceptance_state)
    return PickupTask(
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        shipment_id=row.shipment_id,  # type: ignore[arg-type]
        assigned_driver_user_id=row.assigned_driver_user_id,
        assigned_batch_id=row.assigned_batch_id,  # type: ignore[arg-type]
        status=PickupTaskStatus(row.status),
        attempt_number=row.attempt_number,
        root_attempt_id=row.root_attempt_id,  # type: ignore[arg-type]
        parent_attempt_id=row.parent_attempt_id,  # type: ignore[arg-type]
        superseded_by_task_id=row.superseded_by_task_id,  # type: ignore[arg-type]
        scheduled_window_start=row.scheduled_window_start,  # type: ignore[arg-type]
        scheduled_window_end=row.scheduled_window_end,  # type: ignore[arg-type]
        acceptance_state=acceptance,
        recovery_reason=row.recovery_reason,
        created_at=row.created_at,  # type: ignore[arg-type]
        recovered_at=row.recovered_at,  # type: ignore[arg-type]
        cancelled_at=row.cancelled_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _history_to_row(entry: RecoveryHistoryEntry) -> RecoveryHistoryRow:
    return RecoveryHistoryRow(
        history_id=entry.history_id,
        pickup_task_id=entry.pickup_task_id,
        replacement_task_id=entry.replacement_task_id,
        action=entry.action.value,
        reason=entry.reason,
        idempotency_key=entry.idempotency_key,
        occurred_at=entry.occurred_at,
    )


def _history_from_row(row: RecoveryHistoryRow) -> RecoveryHistoryEntry:
    return RecoveryHistoryEntry(
        history_id=row.history_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        replacement_task_id=row.replacement_task_id,  # type: ignore[arg-type]
        action=RecoveryAction(row.action),
        reason=row.reason,
        idempotency_key=row.idempotency_key,
        occurred_at=row.occurred_at,  # type: ignore[arg-type]
    )


def _idempotency_to_row(record: IdempotencyRecord) -> RecoveryIdempotencyRow:
    return RecoveryIdempotencyRow(
        idempotency_key=record.idempotency_key,
        command_fingerprint=record.command_fingerprint,
        pickup_task_id=record.pickup_task_id,
        action=record.action.value,
        original_task_id=record.original_task_id,
        result_task_id=record.result_task_id,
        recorded_at=record.recorded_at,
    )


def _idempotency_from_row(row: RecoveryIdempotencyRow) -> IdempotencyRecord:
    return IdempotencyRecord(
        idempotency_key=row.idempotency_key,
        command_fingerprint=row.command_fingerprint,
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        action=RecoveryAction(row.action),
        original_task_id=row.original_task_id,  # type: ignore[arg-type]
        result_task_id=row.result_task_id,  # type: ignore[arg-type]
        recorded_at=row.recorded_at,  # type: ignore[arg-type]
    )
