"""Repository and unit-of-work ports for Pickup recovery and acceptance."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
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
)
from pickup.domain.publish import PublishResult
from pickup.domain.workforce import DriverWorkSession


class PickupTaskRepository(Protocol):
    """Persistence boundary owned by the Pickup service."""

    def save_pickup_task(self, pickup_task: PickupTask) -> None: ...

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTask | None: ...

    def list_tasks_for_shipment(self, shipment_id: UUID) -> tuple[PickupTask, ...]: ...

    def list_tasks_for_driver(self, driver_user_id: str) -> tuple[PickupTask, ...]: ...


class RecoveryHistoryRepository(Protocol):
    """Append-only recovery history store."""

    def append_entry(self, entry: RecoveryHistoryEntry) -> None: ...

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[RecoveryHistoryEntry, ...]: ...


class IdempotencyRepository(Protocol):
    """Recovery command idempotency store."""

    def save_record(self, record: IdempotencyRecord) -> None: ...

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None: ...


class AcceptanceIdempotencyRepository(Protocol):
    """Acceptance command idempotency store — carries stable event_id."""

    def save_record(self, record: AcceptanceIdempotencyRecord) -> None: ...

    def get_record(self, idempotency_key: str) -> AcceptanceIdempotencyRecord | None: ...


class OutboxRepository(Protocol):
    """Transactional integration outbox store."""

    def insert(self, record: OutboxRecord) -> None: ...

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None: ...

    def list_pending(self) -> tuple[OutboxRecord, ...]: ...

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]: ...


class OutboxRelayStorePort(Protocol):
    """Lease-based outbox relay store — claim commits before broker await."""

    def recover_stale_processing(self, *, now: datetime) -> int: ...

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[OutboxRecord]: ...

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
    ) -> None: ...

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None: ...


class PublisherPort(Protocol):
    """JetStream publisher boundary."""

    def publish(
        self,
        *,
        subject: str,
        payload_json: dict,
        transport_msg_id: str,
    ) -> PublishResult:
        """Return publish outcome with broker ACK status."""


class RecoveryUnitOfWork(Protocol):
    """Atomic recovery boundary — one transaction for all recovery effects."""

    pickup_tasks: PickupTaskRepository
    recovery_history: RecoveryHistoryRepository
    idempotency: IdempotencyRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class AcceptanceUnitOfWork(Protocol):
    """Atomic acceptance boundary — task mutation + outbox insert together."""

    pickup_tasks: PickupTaskRepository
    outbox: OutboxRepository
    acceptance_idempotency: AcceptanceIdempotencyRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class TaskHistoryRepository(Protocol):
    """Append-only Pickup audit history with explicit actor identity."""

    def append_entry(self, entry: TaskHistoryEntry) -> None: ...

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[TaskHistoryEntry, ...]: ...


class DriverWorkSessionRepository(Protocol):
    """Driver pickup-capability availability store."""

    def save_session(self, session: DriverWorkSession) -> None: ...

    def get_session(self, session_id: UUID) -> DriverWorkSession | None: ...

    def get_open_session_for_driver(self, driver_user_id: str) -> DriverWorkSession | None:
        """Return the driver's single open session, locking the driver row for update."""


class CourierChallengeRepository(Protocol):
    """Sender-verification challenge store."""

    def save_challenge(self, challenge: CourierChallenge) -> None: ...

    def get_challenge(self, challenge_id: UUID) -> CourierChallenge | None: ...

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierChallenge, ...]: ...


class CourierManifestRepository(Protocol):
    """Sender-confirmed parcel manifest store."""

    def save_manifest(self, manifest: CourierManifest) -> None: ...

    def get_manifest(self, manifest_id: UUID) -> CourierManifest | None: ...

    def list_open_for_task(self, pickup_task_id: UUID) -> tuple[CourierManifest, ...]: ...


class HandoverManifestRepository(Protocol):
    """Driver-to-hub handover manifest store."""

    def save_manifest(self, manifest: HandoverManifest) -> None: ...

    def get_manifest(self, manifest_id: UUID) -> HandoverManifest | None: ...

    def list_active_for_driver(self, driver_user_id: str) -> tuple[HandoverManifest, ...]: ...

    def save_item(self, item: HandoverManifestItem) -> None: ...

    def list_items(self, manifest_id: UUID) -> tuple[HandoverManifestItem, ...]: ...

    def find_active_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        """Line on a non-terminal manifest — used to block double-listing a parcel."""

    def find_custody_item_for_shipment(
        self, shipment_id: UUID
    ) -> HandoverManifestItem | None:
        """Most recent line for the parcel on any manifest — custody source of truth."""


class OfflineAuthorizationRepository(Protocol):
    """Signed offline work authorization store."""

    def save_authorization(self, authorization: OfflineAuthorization) -> None: ...

    def get_authorization(self, authorization_id: UUID) -> OfflineAuthorization | None: ...

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineAuthorization, ...]: ...


class OfflineStreamRepository(Protocol):
    """Per-device append-only capture stream store."""

    def save_stream(self, stream: OfflineStream) -> None: ...

    def get_stream(self, stream_id: UUID) -> OfflineStream | None: ...


class OfflineEventRepository(Protocol):
    """Preserved offline captures — every submission is retained."""

    def append_event(self, event: OfflineEventRecord) -> None: ...

    def update_event(self, event: OfflineEventRecord) -> None: ...

    def find_by_operation_id(
        self, *, driver_user_id: str, operation_id: UUID
    ) -> OfflineEventRecord | None: ...

    def find_by_stream_sequence(
        self, *, stream_id: UUID, sequence: int
    ) -> OfflineEventRecord | None: ...

    def list_for_driver(self, driver_user_id: str) -> tuple[OfflineEventRecord, ...]: ...


class ReconciliationCaseRepository(Protocol):
    """Operations-owned offline reconciliation case store."""

    def save_case(self, case: OfflineReconciliationCase) -> None: ...

    def get_case(self, case_id: UUID) -> OfflineReconciliationCase | None: ...

    def get_open_case_for_event(
        self, offline_event_row_id: UUID
    ) -> OfflineReconciliationCase | None: ...

    def list_cases(
        self, *, driver_user_id: str | None = None, status: str | None = None
    ) -> tuple[OfflineReconciliationCase, ...]: ...


class DriverWorkUnitOfWork(Protocol):
    """Atomic driver-workforce and task-progress boundary."""

    pickup_tasks: PickupTaskRepository
    work_sessions: DriverWorkSessionRepository
    task_history: TaskHistoryRepository
    handover_manifests: HandoverManifestRepository
    offline_events: OfflineEventRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class HandoverUnitOfWork(Protocol):
    """Atomic sender-ceremony and hub-handover boundary including the outbox."""

    pickup_tasks: PickupTaskRepository
    task_history: TaskHistoryRepository
    challenges: CourierChallengeRepository
    courier_manifests: CourierManifestRepository
    handover_manifests: HandoverManifestRepository
    outbox: OutboxRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class OfflineUnitOfWork(Protocol):
    """Atomic offline authorization, replay, and reconciliation boundary."""

    pickup_tasks: PickupTaskRepository
    task_history: TaskHistoryRepository
    work_sessions: DriverWorkSessionRepository
    offline_authorizations: OfflineAuthorizationRepository
    offline_streams: OfflineStreamRepository
    offline_events: OfflineEventRepository
    reconciliation_cases: ReconciliationCaseRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def savepoint(self) -> NestedTransaction:
        """Nested boundary so one failed replay does not discard the whole batch."""


class NestedTransaction(Protocol):
    """Savepoint handle used to isolate a single offline command replay."""

    def __enter__(self) -> NestedTransaction: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool: ...
