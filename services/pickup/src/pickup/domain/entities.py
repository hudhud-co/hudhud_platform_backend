"""Core Pickup domain entities — task recovery and attempt history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pickup.domain.value_objects import (
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    RecoveryAction,
)


@dataclass(slots=True)
class PickupTask:
    """Pickup task with explicit attempt lineage and recovery metadata."""

    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    assigned_batch_id: UUID
    status: PickupTaskStatus
    attempt_number: int
    root_attempt_id: UUID
    parent_attempt_id: UUID | None
    superseded_by_task_id: UUID | None
    scheduled_window_start: datetime | None
    scheduled_window_end: datetime | None
    acceptance_state: PickupTaskAcceptanceState | None
    recovery_reason: str | None
    created_at: datetime
    recovered_at: datetime | None
    cancelled_at: datetime | None
    version: int

    @property
    def is_terminal(self) -> bool:
        return self.status in (PickupTaskStatus.SUPERSEDED, PickupTaskStatus.CANCELLED)

    @property
    def is_accepted(self) -> bool:
        return self.acceptance_state in (
            PickupTaskAcceptanceState.ACCEPTED,
            PickupTaskAcceptanceState.ACCEPTED_WITH_EXCEPTION,
        )


@dataclass(frozen=True, slots=True)
class RecoveryHistoryEntry:
    """Append-only recovery audit record."""

    history_id: UUID
    pickup_task_id: UUID
    replacement_task_id: UUID | None
    action: RecoveryAction
    reason: str | None
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Persisted recovery command outcome for exactly-once semantics."""

    idempotency_key: str
    command_fingerprint: str
    pickup_task_id: UUID
    action: RecoveryAction
    original_task_id: UUID
    result_task_id: UUID | None
    recorded_at: datetime
