"""PostgreSQL Pickup unit-of-work adapter with optimistic concurrency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from pickup.domain.entities import (
    AcceptanceIdempotencyRecord,
    IdempotencyRecord,
    OutboxRecord,
    PickupTask,
    RecoveryHistoryEntry,
)
from pickup.domain.errors import StalePickupTaskVersion
from pickup.domain.value_objects import (
    OutboxStatus,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    RecoveryAction,
)
from pickup.infrastructure.persistence.models import (
    AcceptanceIdempotencyRow,
    IntegrationOutboxRow,
    PickupTaskRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
)


@dataclass
class SqlAlchemyPickupUnitOfWork:
    """Transactional Pickup boundary — recovery and acceptance effects."""

    session_factory: sessionmaker[Session]
    _session: Session | None = None
    _pending_tasks: dict[UUID, tuple[PickupTask, int | None]] | None = None
    _pending_history: list[RecoveryHistoryEntry] | None = None
    _pending_idempotency: dict[str, IdempotencyRecord] | None = None
    _pending_acceptance_idempotency: dict[str, AcceptanceIdempotencyRecord] | None = None
    _pending_outbox: list[OutboxRecord] | None = None

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

    def begin(self) -> None:
        self._session = self.session_factory()
        self._pending_tasks = {}
        self._pending_history = []
        self._pending_idempotency = {}
        self._pending_acceptance_idempotency = {}
        self._pending_outbox = []

    def commit(self) -> None:
        if self._session is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        assert self._pending_tasks is not None
        assert self._pending_history is not None
        assert self._pending_idempotency is not None
        assert self._pending_acceptance_idempotency is not None
        assert self._pending_outbox is not None

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

        for record in self._pending_acceptance_idempotency.values():
            session.add(_acceptance_idempotency_to_row(record))

        for record in self._pending_outbox:
            session.add(_outbox_to_row(record))

        session.commit()
        self._session.close()
        self._clear_tx()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._clear_tx()

    def _clear_tx(self) -> None:
        self._session = None
        self._pending_tasks = None
        self._pending_history = None
        self._pending_idempotency = None
        self._pending_acceptance_idempotency = None
        self._pending_outbox = None

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

    def _working_acceptance_idempotency(self) -> dict[str, AcceptanceIdempotencyRecord]:
        if self._pending_acceptance_idempotency is None:
            msg = "acceptance idempotency mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_acceptance_idempotency

    def _working_outbox(self) -> list[OutboxRecord]:
        if self._pending_outbox is None:
            msg = "outbox mutation outside transaction"
            raise RuntimeError(msg)
        return self._pending_outbox

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


# Backward-compatible alias for composition root / recovery wiring.
SqlAlchemyRecoveryUnitOfWork = SqlAlchemyPickupUnitOfWork


class _PickupTaskRepo:
    def __init__(self, store: SqlAlchemyPickupUnitOfWork) -> None:
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
    def __init__(self, store: SqlAlchemyPickupUnitOfWork) -> None:
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
    def __init__(self, store: SqlAlchemyPickupUnitOfWork) -> None:
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


class _AcceptanceIdempotencyRepo:
    def __init__(self, store: SqlAlchemyPickupUnitOfWork) -> None:
        self._store = store

    def save_record(self, record: AcceptanceIdempotencyRecord) -> None:
        if self._store._in_transaction():
            self._store._working_acceptance_idempotency()[record.idempotency_key] = record
            return
        msg = "acceptance idempotency save outside transaction"
        raise RuntimeError(msg)

    def get_record(self, idempotency_key: str) -> AcceptanceIdempotencyRecord | None:
        pending = self._store._pending_acceptance_idempotency
        if pending is not None and idempotency_key in pending:
            return pending[idempotency_key]
        if self._store._session is not None:
            row = self._store._session.get(AcceptanceIdempotencyRow, idempotency_key)
            return _acceptance_idempotency_from_row(row) if row is not None else None
        with self._store.session_factory() as session:
            row = session.get(AcceptanceIdempotencyRow, idempotency_key)
            return _acceptance_idempotency_from_row(row) if row is not None else None


class _OutboxRepo:
    def __init__(self, store: SqlAlchemyPickupUnitOfWork) -> None:
        self._store = store

    def insert(self, record: OutboxRecord) -> None:
        if self._store._in_transaction():
            self._store._working_outbox().append(record)
            return
        msg = "outbox insert outside transaction"
        raise RuntimeError(msg)

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        pending = self._store._pending_outbox
        if pending is not None:
            for record in pending:
                if record.event_id == event_id:
                    return record
        if self._store._session is not None:
            row = self._store._session.execute(
                select(IntegrationOutboxRow).where(IntegrationOutboxRow.event_id == event_id)
            ).scalar_one_or_none()
            return _outbox_from_row(row) if row is not None else None
        with self._store.session_factory() as session:
            row = session.execute(
                select(IntegrationOutboxRow).where(IntegrationOutboxRow.event_id == event_id)
            ).scalar_one_or_none()
            return _outbox_from_row(row) if row is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        records: list[OutboxRecord] = []
        if self._store._session is not None:
            rows = self._store._session.execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.status == OutboxStatus.PENDING.value
                )
            ).scalars()
            records.extend(_outbox_from_row(row) for row in rows)
            pending = self._store._pending_outbox
            if pending is not None:
                records.extend(
                    record for record in pending if record.status is OutboxStatus.PENDING
                )
        else:
            with self._store.session_factory() as session:
                rows = session.execute(
                    select(IntegrationOutboxRow).where(
                        IntegrationOutboxRow.status == OutboxStatus.PENDING.value
                    )
                ).scalars()
                records.extend(_outbox_from_row(row) for row in rows)
        return tuple(records)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        records: list[OutboxRecord] = []
        if self._store._session is not None:
            rows = self._store._session.execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.aggregate_id == aggregate_id
                )
            ).scalars()
            records.extend(_outbox_from_row(row) for row in rows)
            pending = self._store._pending_outbox
            if pending is not None:
                records.extend(
                    record for record in pending if record.aggregate_id == aggregate_id
                )
        else:
            with self._store.session_factory() as session:
                rows = session.execute(
                    select(IntegrationOutboxRow).where(
                        IntegrationOutboxRow.aggregate_id == aggregate_id
                    )
                ).scalars()
                records.extend(_outbox_from_row(row) for row in rows)
        return tuple(records)


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
        has_pickup_condition_proof=task.has_pickup_condition_proof,
        accepted_at=task.accepted_at,
        accepted_by_driver_user_id=task.accepted_by_driver_user_id,
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
        has_pickup_condition_proof=task.has_pickup_condition_proof,
        accepted_at=task.accepted_at,
        accepted_by_driver_user_id=task.accepted_by_driver_user_id,
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
        "has_pickup_condition_proof": task.has_pickup_condition_proof,
        "accepted_at": task.accepted_at,
        "accepted_by_driver_user_id": task.accepted_by_driver_user_id,
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
        has_pickup_condition_proof=bool(row.has_pickup_condition_proof),
        accepted_at=row.accepted_at,  # type: ignore[arg-type]
        accepted_by_driver_user_id=row.accepted_by_driver_user_id,
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


def _acceptance_idempotency_to_row(
    record: AcceptanceIdempotencyRecord,
) -> AcceptanceIdempotencyRow:
    return AcceptanceIdempotencyRow(
        idempotency_key=record.idempotency_key,
        command_fingerprint=record.command_fingerprint,
        pickup_task_id=record.pickup_task_id,
        event_id=record.event_id,
        recorded_at=record.recorded_at,
    )


def _acceptance_idempotency_from_row(
    row: AcceptanceIdempotencyRow,
) -> AcceptanceIdempotencyRecord:
    return AcceptanceIdempotencyRecord(
        idempotency_key=row.idempotency_key,
        command_fingerprint=row.command_fingerprint,
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        event_id=row.event_id,  # type: ignore[arg-type]
        recorded_at=row.recorded_at,  # type: ignore[arg-type]
    )


def _outbox_to_row(record: OutboxRecord) -> IntegrationOutboxRow:
    return IntegrationOutboxRow(
        id=record.id,
        event_id=record.event_id,
        subject=record.subject,
        event_type=record.event_type,
        event_version=record.event_version,
        aggregate_id=record.aggregate_id,
        aggregate_version=record.aggregate_version,
        payload_json=record.payload_json,
        status=record.status.value,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        next_attempt_at=record.next_attempt_at,
        processing_owner=record.processing_owner,
        processing_until=record.processing_until,
        published_at=record.published_at,
        last_error_code=record.last_error_code,
        last_error_message=record.last_error_message,
        created_at=record.created_at,
    )


def _outbox_from_row(row: IntegrationOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,  # type: ignore[arg-type]
        event_id=row.event_id,  # type: ignore[arg-type]
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,  # type: ignore[arg-type]
        aggregate_version=row.aggregate_version,
        payload_json=dict(row.payload_json),
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,  # type: ignore[arg-type]
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,  # type: ignore[arg-type]
        published_at=row.published_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,  # type: ignore[arg-type]
    )


@dataclass
class SqlAlchemyOutboxRelayStore:
    """Lease-based outbox relay store — claim commits before broker await."""

    session_factory: sessionmaker[Session]

    def recover_stale_processing(self, *, now: datetime) -> int:
        with self.session_factory() as session:
            result = session.execute(
                update(IntegrationOutboxRow)
                .where(
                    IntegrationOutboxRow.status == OutboxStatus.PROCESSING.value,
                    IntegrationOutboxRow.processing_until < now,
                )
                .values(
                    status=OutboxStatus.PENDING.value,
                    processing_owner=None,
                    processing_until=None,
                    next_attempt_at=now,
                    last_error_code="STALE_LEASE",
                    last_error_message="stale_processing_lease",
                )
            )
            session.commit()
            return result.rowcount or 0

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]:
        with self.session_factory() as session:
            candidate_ids = (
                session.execute(
                    select(IntegrationOutboxRow.id)
                    .where(
                        IntegrationOutboxRow.status == OutboxStatus.PENDING.value,
                        IntegrationOutboxRow.next_attempt_at <= now,
                    )
                    .order_by(IntegrationOutboxRow.next_attempt_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            if not candidate_ids:
                return []
            session.execute(
                update(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.id.in_(candidate_ids))
                .values(
                    status=OutboxStatus.PROCESSING.value,
                    processing_owner=owner,
                    processing_until=lease_until,
                    attempt_count=IntegrationOutboxRow.attempt_count + 1,
                )
            )
            rows = session.execute(
                select(IntegrationOutboxRow).where(IntegrationOutboxRow.id.in_(candidate_ids))
            ).scalars()
            claimed = [_outbox_from_row(row) for row in rows]
            session.commit()
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
        with self.session_factory() as session:
            row = session.get(IntegrationOutboxRow, outbox_id)
            if row is None:
                msg = f"outbox row missing: {outbox_id}"
                raise KeyError(msg)
            row.status = status
            if published_at is not None:
                row.published_at = published_at
            if next_attempt_at is not None:
                row.next_attempt_at = next_attempt_at
            row.last_error_code = last_error_code
            row.last_error_message = last_error_message
            if clear_owner:
                row.processing_owner = None
            if clear_lease:
                row.processing_until = None
            session.commit()

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        with self.session_factory() as session:
            row = session.execute(
                select(IntegrationOutboxRow).where(IntegrationOutboxRow.event_id == event_id)
            ).scalar_one_or_none()
            return _outbox_from_row(row) if row is not None else None
