"""In-memory Pickup persistence with rollback-safe unit of work.

Transactions snapshot every collection on ``begin`` and swap them in on ``commit``, so a
rollback leaves no partial writes — the same all-or-nothing guarantee the PostgreSQL
adapter provides. ``savepoint`` gives offline replay per-command isolation inside one
batch.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from pickup.domain.entities import (
    AcceptanceIdempotencyRecord,
    IdempotencyRecord,
    OutboxRecord,
    PickupTask,
    RecoveryHistoryEntry,
    TaskHistoryEntry,
)
from pickup.domain.handover import (
    ACTIVE_HANDOVER_MANIFEST_STATUSES,
    CourierChallenge,
    CourierManifest,
    HandoverManifest,
    HandoverManifestItem,
)
from pickup.domain.offline import (
    OfflineAuthorization,
    OfflineEventRecord,
    OfflineReconciliationCase,
    OfflineStream,
    ReconciliationCaseStatus,
)
from pickup.domain.value_objects import OutboxStatus
from pickup.domain.workforce import OPEN_WORK_SESSION_STATUSES, DriverWorkSession

_COLLECTIONS: tuple[str, ...] = (
    "pickup_tasks",
    "recovery_history",
    "idempotency",
    "acceptance_idempotency",
    "outbox",
    "outbox_by_event_id",
    "task_history",
    "work_sessions",
    "challenges",
    "courier_manifests",
    "handover_manifests",
    "handover_items",
    "offline_authorizations",
    "offline_streams",
    "offline_events",
    "reconciliation_cases",
)

_LIST_COLLECTIONS: frozenset[str] = frozenset({"recovery_history", "task_history"})


class SimulatedCommitFailure(RuntimeError):
    """Test hook: forces rollback before commit completes."""


class InMemoryPickupUnitOfWork:
    """Rollback-safe in-memory UoW covering every Pickup aggregate."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._committed: dict[str, Any] = {
            name: [] if name in _LIST_COLLECTIONS else {} for name in _COLLECTIONS
        }
        self._tx: dict[str, Any] | None = None
        self.fail_on_commit = False
        self.actions: list[str] = []

    # ------------------------------------------------------------- repositories

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

    @property
    def task_history(self) -> _TaskHistoryRepo:
        return _TaskHistoryRepo(self)

    @property
    def work_sessions(self) -> _WorkSessionRepo:
        return _WorkSessionRepo(self)

    @property
    def challenges(self) -> _ChallengeRepo:
        return _ChallengeRepo(self)

    @property
    def courier_manifests(self) -> _CourierManifestRepo:
        return _CourierManifestRepo(self)

    @property
    def handover_manifests(self) -> _HandoverManifestRepo:
        return _HandoverManifestRepo(self)

    @property
    def offline_authorizations(self) -> _OfflineAuthorizationRepo:
        return _OfflineAuthorizationRepo(self)

    @property
    def offline_streams(self) -> _OfflineStreamRepo:
        return _OfflineStreamRepo(self)

    @property
    def offline_events(self) -> _OfflineEventRepo:
        return _OfflineEventRepo(self)

    @property
    def reconciliation_cases(self) -> _ReconciliationCaseRepo:
        return _ReconciliationCaseRepo(self)

    # ------------------------------------------------------------- relay façade

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

    # ------------------------------------------------------------- transactions

    def begin(self) -> None:
        if self._tx is not None:
            # The lock below serialises *different* callers; a second begin from the
            # one already holding it would deadlock instead of failing. Raise the same
            # message the SQLAlchemy store raises, so a leaked transaction looks the
            # same in a unit test as it does in production.
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._lock.acquire()
        self._tx = copy.deepcopy(self._committed)
        self.actions.append("begin")

    def commit(self) -> None:
        if self._tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        try:
            if self.fail_on_commit:
                self._tx = None
                self.actions.append("rollback")
                raise SimulatedCommitFailure("simulated commit failure")
            self._committed = self._tx
            self._tx = None
            self.actions.append("commit")
        finally:
            self._lock.release()

    def rollback(self) -> None:
        had_open_tx = self._tx is not None
        self._tx = None
        self.actions.append("rollback")
        if had_open_tx:
            self._lock.release()

    def savepoint(self) -> _MemorySavepoint:
        """Isolate one command: on error, restore the pre-command transaction state."""
        if self._tx is None:
            msg = "savepoint requires an open transaction"
            raise RuntimeError(msg)
        return _MemorySavepoint(self)

    # ------------------------------------------------------------------ internal

    def _working(self, name: str) -> Any:
        state = self._tx if self._tx is not None else self._committed
        return state[name]

    def _durable(self, name: str) -> Any:
        """Relay operations run outside the command transaction, like the real relay."""
        return self._committed[name]

    @property
    def _outbox(self) -> dict[UUID, OutboxRecord]:
        """Durable outbox rows — relay tests manipulate committed state directly."""
        return self._committed["outbox"]

    @property
    def _pickup_tasks(self) -> dict[UUID, PickupTask]:
        return self._committed["pickup_tasks"]


class _MemorySavepoint:
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
        self._store = store
        self._snapshot: dict[str, Any] | None = None

    def __enter__(self) -> _MemorySavepoint:
        self._snapshot = copy.deepcopy(self._store._tx)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is not None and self._snapshot is not None:
            self._store._tx = self._snapshot
        self._snapshot = None
        return False


# Backward-compatible alias for recovery tests.
InMemoryRecoveryUnitOfWork = InMemoryPickupUnitOfWork


class _Repo:
    def __init__(self, store: InMemoryPickupUnitOfWork) -> None:
        self._store = store


class _PickupTaskRepo(_Repo):
    def save_pickup_task(self, pickup_task: PickupTask) -> None:
        self._store._working("pickup_tasks")[pickup_task.pickup_task_id] = copy.deepcopy(
            pickup_task
        )

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTask | None:
        task = self._store._working("pickup_tasks").get(pickup_task_id)
        return copy.deepcopy(task) if task is not None else None

    def list_tasks_for_shipment(self, shipment_id: UUID) -> tuple[PickupTask, ...]:
        return tuple(
            copy.deepcopy(task)
            for task in self._store._working("pickup_tasks").values()
            if task.shipment_id == shipment_id
        )

    def list_tasks_for_driver(self, driver_user_id: str) -> tuple[PickupTask, ...]:
        return tuple(
            copy.deepcopy(task)
            for task in self._store._working("pickup_tasks").values()
            if task.assigned_driver_user_id == driver_user_id
        )


class _RecoveryHistoryRepo(_Repo):
    def append_entry(self, entry: RecoveryHistoryEntry) -> None:
        self._store._working("recovery_history").append(copy.deepcopy(entry))

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[RecoveryHistoryEntry, ...]:
        return tuple(
            copy.deepcopy(entry)
            for entry in self._store._working("recovery_history")
            if entry.pickup_task_id == pickup_task_id
        )


class _IdempotencyRepo(_Repo):
    def save_record(self, record: IdempotencyRecord) -> None:
        self._store._working("idempotency")[record.idempotency_key] = copy.deepcopy(record)

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        record = self._store._working("idempotency").get(idempotency_key)
        return copy.deepcopy(record) if record is not None else None


class _AcceptanceIdempotencyRepo(_Repo):
    def save_record(self, record: AcceptanceIdempotencyRecord) -> None:
        self._store._working("acceptance_idempotency")[record.idempotency_key] = copy.deepcopy(
            record
        )

    def get_record(self, idempotency_key: str) -> AcceptanceIdempotencyRecord | None:
        record = self._store._working("acceptance_idempotency").get(idempotency_key)
        return copy.deepcopy(record) if record is not None else None


class _TaskHistoryRepo(_Repo):
    def append_entry(self, entry: TaskHistoryEntry) -> None:
        self._store._working("task_history").append(copy.deepcopy(entry))

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[TaskHistoryEntry, ...]:
        return tuple(
            copy.deepcopy(entry)
            for entry in self._store._working("task_history")
            if entry.pickup_task_id == pickup_task_id
        )


class _WorkSessionRepo(_Repo):
    def save_session(self, session: DriverWorkSession) -> None:
        self._store._working("work_sessions")[session.session_id] = copy.deepcopy(session)

    def get_session(self, session_id: UUID) -> DriverWorkSession | None:
        session = self._store._working("work_sessions").get(session_id)
        return copy.deepcopy(session) if session is not None else None

    def get_open_session_for_driver(self, driver_user_id: str) -> DriverWorkSession | None:
        for session in self._store._working("work_sessions").values():
            if session.driver_user_id != driver_user_id:
                continue
            if session.status in OPEN_WORK_SESSION_STATUSES:
                return copy.deepcopy(session)
        return None


class _ChallengeRepo(_Repo):
    def save_challenge(self, challenge: CourierChallenge) -> None:
        self._store._working("challenges")[challenge.challenge_id] = copy.deepcopy(challenge)

    def get_challenge(self, challenge_id: UUID) -> CourierChallenge | None:
        challenge = self._store._working("challenges").get(challenge_id)
        return copy.deepcopy(challenge) if challenge is not None else None

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierChallenge, ...]:
        return tuple(
            copy.deepcopy(challenge)
            for challenge in self._store._working("challenges").values()
            if challenge.pickup_task_id == pickup_task_id and challenge.is_open
        )


class _CourierManifestRepo(_Repo):
    def save_manifest(self, manifest: CourierManifest) -> None:
        self._store._working("courier_manifests")[manifest.manifest_id] = copy.deepcopy(
            manifest
        )

    def get_manifest(self, manifest_id: UUID) -> CourierManifest | None:
        manifest = self._store._working("courier_manifests").get(manifest_id)
        return copy.deepcopy(manifest) if manifest is not None else None

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierManifest, ...]:
        return tuple(
            copy.deepcopy(manifest)
            for manifest in self._store._working("courier_manifests").values()
            if manifest.pickup_task_id == pickup_task_id and manifest.is_open
        )


class _HandoverManifestRepo(_Repo):
    def save_manifest(self, manifest: HandoverManifest) -> None:
        self._store._working("handover_manifests")[manifest.manifest_id] = copy.deepcopy(
            manifest
        )

    def get_manifest(self, manifest_id: UUID) -> HandoverManifest | None:
        manifest = self._store._working("handover_manifests").get(manifest_id)
        return copy.deepcopy(manifest) if manifest is not None else None

    def list_active_for_driver(self, driver_user_id: str) -> tuple[HandoverManifest, ...]:
        return tuple(
            copy.deepcopy(manifest)
            for manifest in self._store._working("handover_manifests").values()
            if manifest.driver_user_id == driver_user_id
            and manifest.status in ACTIVE_HANDOVER_MANIFEST_STATUSES
        )

    def save_item(self, item: HandoverManifestItem) -> None:
        self._store._working("handover_items")[item.item_id] = copy.deepcopy(item)

    def list_items(self, manifest_id: UUID) -> tuple[HandoverManifestItem, ...]:
        return tuple(
            copy.deepcopy(item)
            for item in self._store._working("handover_items").values()
            if item.manifest_id == manifest_id
        )

    def find_active_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        manifests = self._store._working("handover_manifests")
        for item in self._store._working("handover_items").values():
            if item.shipment_id != shipment_id:
                continue
            manifest = manifests.get(item.manifest_id)
            if manifest is None or not manifest.is_active:
                continue
            return copy.deepcopy(item)
        return None

    def find_custody_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        candidates = [
            item
            for item in self._store._working("handover_items").values()
            if item.shipment_id == shipment_id
        ]
        if not candidates:
            return None
        released = [item for item in candidates if item.releases_custody]
        if released:
            return copy.deepcopy(released[-1])
        # EXPECTED and MISSING both keep the parcel bound to its driver.
        return copy.deepcopy(candidates[-1])


class _OfflineAuthorizationRepo(_Repo):
    def save_authorization(self, authorization: OfflineAuthorization) -> None:
        self._store._working("offline_authorizations")[
            authorization.authorization_id
        ] = copy.deepcopy(authorization)

    def get_authorization(self, authorization_id: UUID) -> OfflineAuthorization | None:
        row = self._store._working("offline_authorizations").get(authorization_id)
        return copy.deepcopy(row) if row is not None else None

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineAuthorization, ...]:
        return tuple(
            copy.deepcopy(row)
            for row in self._store._working("offline_authorizations").values()
            if row.driver_user_id == driver_user_id
        )


class _OfflineStreamRepo(_Repo):
    def save_stream(self, stream: OfflineStream) -> None:
        self._store._working("offline_streams")[stream.stream_id] = copy.deepcopy(stream)

    def get_stream(self, stream_id: UUID) -> OfflineStream | None:
        stream = self._store._working("offline_streams").get(stream_id)
        return copy.deepcopy(stream) if stream is not None else None


class _OfflineEventRepo(_Repo):
    def append_event(self, event: OfflineEventRecord) -> None:
        events = self._store._working("offline_events")
        for existing in events.values():
            if (
                existing.driver_user_id == event.driver_user_id
                and existing.operation_id == event.operation_id
            ):
                msg = f"duplicate offline operation_id: {event.operation_id}"
                raise ValueError(msg)
            if existing.stream_id == event.stream_id and existing.sequence == event.sequence:
                msg = f"duplicate offline sequence: {event.sequence}"
                raise ValueError(msg)
        events[event.event_row_id] = copy.deepcopy(event)

    def update_event(self, event: OfflineEventRecord) -> None:
        self._store._working("offline_events")[event.event_row_id] = copy.deepcopy(event)

    def find_by_operation_id(
        self, *, driver_user_id: str, operation_id: UUID
    ) -> OfflineEventRecord | None:
        for event in self._store._working("offline_events").values():
            if event.driver_user_id == driver_user_id and event.operation_id == operation_id:
                return copy.deepcopy(event)
        return None

    def find_by_stream_sequence(
        self, *, stream_id: UUID, sequence: int
    ) -> OfflineEventRecord | None:
        for event in self._store._working("offline_events").values():
            if event.stream_id == stream_id and event.sequence == sequence:
                return copy.deepcopy(event)
        return None

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineEventRecord, ...]:
        return tuple(
            copy.deepcopy(event)
            for event in self._store._working("offline_events").values()
            if event.driver_user_id == driver_user_id
        )


class _ReconciliationCaseRepo(_Repo):
    def save_case(self, case: OfflineReconciliationCase) -> None:
        self._store._working("reconciliation_cases")[case.case_id] = copy.deepcopy(case)

    def get_case(self, case_id: UUID) -> OfflineReconciliationCase | None:
        case = self._store._working("reconciliation_cases").get(case_id)
        return copy.deepcopy(case) if case is not None else None

    def get_open_case_for_event(
        self, offline_event_row_id: UUID
    ) -> OfflineReconciliationCase | None:
        for case in self._store._working("reconciliation_cases").values():
            if (
                case.offline_event_row_id == offline_event_row_id
                and case.status is ReconciliationCaseStatus.OPEN
            ):
                return copy.deepcopy(case)
        return None

    def list_cases(
        self, *, driver_user_id: str | None = None, status: str | None = None
    ) -> tuple[OfflineReconciliationCase, ...]:
        return tuple(
            copy.deepcopy(case)
            for case in self._store._working("reconciliation_cases").values()
            if (driver_user_id is None or case.driver_user_id == driver_user_id)
            and (status is None or case.status.value == status)
        )


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        outbox = self._store._working("outbox")
        by_event = self._store._working("outbox_by_event_id")
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
        row_id = self._store._working("outbox_by_event_id").get(event_id)
        if row_id is None:
            return None
        record = self._store._working("outbox").get(row_id)
        return copy.deepcopy(record) if record is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.status is OutboxStatus.PENDING
        )

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.aggregate_id == aggregate_id
        )

    def recover_stale_processing(self, *, now: datetime) -> int:
        recovered = 0
        outbox = self._store._durable("outbox")
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
        outbox = self._store._durable("outbox")
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
        outbox = self._store._durable("outbox")
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
