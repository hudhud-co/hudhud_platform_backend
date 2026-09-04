"""Core Pickup domain entities — task recovery, acceptance, and outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pickup.domain.value_objects import (
    OutboxStatus,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    RecoveryAction,
)


@dataclass(slots=True)
class PickupTask:
    """Pickup task with explicit attempt lineage, recovery, and acceptance metadata."""

    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    assigned_batch_id: UUID | None
    status: PickupTaskStatus
    attempt_number: int
    root_attempt_id: UUID
    parent_attempt_id: UUID | None
    superseded_by_task_id: UUID | None
    scheduled_window_start: datetime | None
    scheduled_window_end: datetime | None
    acceptance_state: PickupTaskAcceptanceState | None
    has_pickup_condition_proof: bool
    accepted_at: datetime | None
    accepted_by_driver_user_id: str | None
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


@dataclass(frozen=True, slots=True)
class AcceptanceIdempotencyRecord:
    """Persisted acceptance command outcome including stable event_id."""

    idempotency_key: str
    command_fingerprint: str
    pickup_task_id: UUID
    event_id: UUID
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """Service-owned transactional integration outbox row (ADR-0008)."""

    id: UUID
    event_id: UUID
    subject: str
    event_type: str
    event_version: int
    aggregate_id: UUID
    aggregate_version: int
    payload_json: dict[str, Any]
    status: OutboxStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    processing_owner: str | None
    processing_until: datetime | None
    published_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
