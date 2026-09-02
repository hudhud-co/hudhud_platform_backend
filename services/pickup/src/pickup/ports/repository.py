"""Repository and unit-of-work ports for Pickup recovery lifecycle."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry


class PickupTaskRepository(Protocol):
    """Persistence boundary owned by the Pickup service."""

    def save_pickup_task(self, pickup_task: PickupTask) -> None: ...

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTask | None: ...

    def list_tasks_for_shipment(self, shipment_id: UUID) -> tuple[PickupTask, ...]: ...


class RecoveryHistoryRepository(Protocol):
    """Append-only recovery history store."""

    def append_entry(self, entry: RecoveryHistoryEntry) -> None: ...

    def list_entries_for_task(self, pickup_task_id: UUID) -> tuple[RecoveryHistoryEntry, ...]: ...


class IdempotencyRepository(Protocol):
    """Recovery command idempotency store."""

    def save_record(self, record: IdempotencyRecord) -> None: ...

    def get_record(self, idempotency_key: str) -> IdempotencyRecord | None: ...


class RecoveryUnitOfWork(Protocol):
    """Atomic recovery boundary — one transaction for all recovery effects."""

    pickup_tasks: PickupTaskRepository
    recovery_history: RecoveryHistoryRepository
    idempotency: IdempotencyRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
