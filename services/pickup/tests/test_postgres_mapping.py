"""Fake unit tests for PostgreSQL entity mapping — no live database."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry
from pickup.domain.value_objects import PickupTaskStatus, RecoveryAction
from pickup.infrastructure.persistence.models import (
    PickupTaskRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
)
from pickup.infrastructure.persistence.sqlalchemy_store import (
    _history_from_row,
    _history_to_row,
    _idempotency_from_row,
    _idempotency_to_row,
    _task_from_row,
    _task_to_row,
)


def test_pickup_task_round_trip_preserves_lineage_fields() -> None:
    task_id = uuid4()
    shipment_id = uuid4()
    batch_id = uuid4()
    root_id = task_id
    parent_id = uuid4()
    superseded_id = uuid4()
    created_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    recovered_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    entity = PickupTask(
        pickup_task_id=task_id,
        shipment_id=shipment_id,
        assigned_driver_user_id="driver-42",
        assigned_batch_id=batch_id,
        status=PickupTaskStatus.SUPERSEDED,
        attempt_number=2,
        root_attempt_id=root_id,
        parent_attempt_id=parent_id,
        superseded_by_task_id=superseded_id,
        scheduled_window_start=created_at,
        scheduled_window_end=recovered_at,
        acceptance_state=None,
        recovery_reason="driver unavailable",
        created_at=created_at,
        recovered_at=recovered_at,
        cancelled_at=None,
        version=2,
    )

    row = _task_to_row(entity)
    restored = _task_from_row(row)

    assert restored == entity
    assert isinstance(row, PickupTaskRow)
    assert row.root_attempt_id == root_id
    assert row.parent_attempt_id == parent_id
    assert row.superseded_by_task_id == superseded_id


def test_recovery_history_and_idempotency_round_trip() -> None:
    history_id = uuid4()
    pickup_task_id = uuid4()
    replacement_task_id = uuid4()
    occurred_at = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)

    history = RecoveryHistoryEntry(
        history_id=history_id,
        pickup_task_id=pickup_task_id,
        replacement_task_id=replacement_task_id,
        action=RecoveryAction.RETRY,
        reason="retry",
        idempotency_key="retry-1",
        occurred_at=occurred_at,
    )
    history_row = _history_to_row(history)
    assert _history_from_row(history_row) == history
    assert isinstance(history_row, RecoveryHistoryRow)

    record = IdempotencyRecord(
        idempotency_key="retry-1",
        command_fingerprint="abc123",
        pickup_task_id=pickup_task_id,
        action=RecoveryAction.RETRY,
        original_task_id=pickup_task_id,
        result_task_id=replacement_task_id,
        recorded_at=occurred_at,
    )
    idempotency_row = _idempotency_to_row(record)
    assert _idempotency_from_row(idempotency_row) == record
    assert isinstance(idempotency_row, RecoveryIdempotencyRow)
