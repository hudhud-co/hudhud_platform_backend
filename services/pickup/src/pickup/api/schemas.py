"""HTTP request/response schemas for Pickup recovery commands."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScheduledWindowRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime


class RecoveryCommandRequest(BaseModel):
    """Shared recovery command body — never carries actor identity."""

    model_config = ConfigDict(frozen=True)

    reason: str | None = None
    expected_version: int | None = Field(default=None, ge=1)


class RescheduleCommandRequest(RecoveryCommandRequest):
    scheduled_window: ScheduledWindowRequest


class ReassignCommandRequest(RecoveryCommandRequest):
    new_driver_user_id: str = Field(min_length=1, max_length=128)


class PickupTaskResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    pickup_task_id: UUID
    shipment_id: UUID
    assigned_driver_user_id: str
    assigned_batch_id: UUID
    status: str
    attempt_number: int
    root_attempt_id: UUID
    parent_attempt_id: UUID | None
    superseded_by_task_id: UUID | None
    scheduled_window_start: datetime | None
    scheduled_window_end: datetime | None
    acceptance_state: str | None
    recovery_reason: str | None
    created_at: datetime
    recovered_at: datetime | None
    cancelled_at: datetime | None
    version: int


class RecoveryHistoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    history_id: UUID
    pickup_task_id: UUID
    replacement_task_id: UUID | None
    action: str
    reason: str | None
    idempotency_key: str
    occurred_at: datetime


class RecoveryCommandResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: str
    original_task: PickupTaskResponse
    replacement_task: PickupTaskResponse | None
    history_entry: RecoveryHistoryResponse
    idempotent_replay: bool
