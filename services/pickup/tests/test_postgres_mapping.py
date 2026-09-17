"""Fake unit tests for PostgreSQL entity mapping — no live database."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

from pickup.domain.entities import (
    AcceptanceIdempotencyRecord,
    IdempotencyRecord,
    OutboxRecord,
    PickupTask,
    RecoveryHistoryEntry,
)
from pickup.domain.value_objects import (
    AssignmentDeclineReason,
    AssignmentState,
    OutboxStatus,
    PickupExceptionReason,
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
from pickup.infrastructure.persistence.sqlalchemy_store import (
    _acceptance_idempotency_from_row,
    _acceptance_idempotency_to_row,
    _history_from_row,
    _history_to_row,
    _idempotency_from_row,
    _idempotency_to_row,
    _outbox_from_row,
    _outbox_to_row,
    _task_from_row,
    _task_to_row,
    _task_update_values,
)


def test_pickup_task_round_trip_preserves_lineage_and_acceptance_fields() -> None:
    task_id = uuid4()
    shipment_id = uuid4()
    batch_id = uuid4()
    root_id = task_id
    parent_id = uuid4()
    superseded_id = uuid4()
    created_at = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    recovered_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    accepted_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

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
        acceptance_state=PickupTaskAcceptanceState.ACCEPTED,
        has_pickup_condition_proof=True,
        accepted_at=accepted_at,
        accepted_by_driver_user_id="driver-42",
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
    assert row.has_pickup_condition_proof is True
    assert row.accepted_by_driver_user_id == "driver-42"


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


def test_outbox_and_acceptance_idempotency_round_trip() -> None:
    event_id = uuid4()
    aggregate_id = uuid4()
    created_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    outbox = OutboxRecord(
        id=uuid4(),
        event_id=event_id,
        subject="hudhud.pickup.pickup.fact.accepted.v1",
        event_type="pickup.fact.accepted",
        event_version=1,
        aggregate_id=aggregate_id,
        aggregate_version=2,
        payload_json={"event_id": str(event_id), "event_type": "pickup.fact.accepted"},
        status=OutboxStatus.PENDING,
        attempt_count=0,
        max_attempts=5,
        next_attempt_at=created_at,
        processing_owner=None,
        processing_until=None,
        published_at=None,
        last_error_code=None,
        last_error_message=None,
        created_at=created_at,
    )
    outbox_row = _outbox_to_row(outbox)
    assert _outbox_from_row(outbox_row) == outbox
    assert isinstance(outbox_row, IntegrationOutboxRow)

    acceptance = AcceptanceIdempotencyRecord(
        idempotency_key="accept-1",
        command_fingerprint="deadbeef",
        pickup_task_id=aggregate_id,
        event_id=event_id,
        recorded_at=created_at,
    )
    acceptance_row = _acceptance_idempotency_to_row(acceptance)
    assert _acceptance_idempotency_from_row(acceptance_row) == acceptance
    assert isinstance(acceptance_row, AcceptanceIdempotencyRow)


def test_pickup_task_mapping_covers_every_entity_field() -> None:
    """A new PickupTask field must reach the row mappers, not silently vanish."""
    entity_fields = {field.name for field in dataclasses.fields(PickupTask)}
    row_columns = set(PickupTaskRow.__table__.columns.keys())
    missing_columns = entity_fields - row_columns
    assert missing_columns == set(), missing_columns

    sample = PickupTask(
        pickup_task_id=uuid4(),
        shipment_id=uuid4(),
        assigned_driver_user_id="driver-42",
        assigned_batch_id=uuid4(),
        status=PickupTaskStatus.PENDING,
        attempt_number=1,
        root_attempt_id=uuid4(),
        parent_attempt_id=None,
        superseded_by_task_id=None,
        scheduled_window_start=None,
        scheduled_window_end=None,
        acceptance_state=None,
        has_pickup_condition_proof=False,
        accepted_at=None,
        accepted_by_driver_user_id=None,
        recovery_reason=None,
        created_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
        recovered_at=None,
        cancelled_at=None,
        version=1,
    )
    # Every column except the primary key participates in the conditional UPDATE.
    updatable = row_columns - {"pickup_task_id"}
    assert set(_task_update_values(sample)) == updatable


def test_pickup_task_round_trip_preserves_driver_lifecycle_fields() -> None:
    task_id = uuid4()
    entity = PickupTask(
        pickup_task_id=task_id,
        shipment_id=uuid4(),
        assigned_driver_user_id="driver-42",
        assigned_batch_id=uuid4(),
        status=PickupTaskStatus.EXCEPTION_REPORTED,
        attempt_number=1,
        root_attempt_id=task_id,
        parent_attempt_id=None,
        superseded_by_task_id=None,
        scheduled_window_start=None,
        scheduled_window_end=None,
        acceptance_state=None,
        has_pickup_condition_proof=True,
        accepted_at=None,
        accepted_by_driver_user_id=None,
        recovery_reason=None,
        created_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
        recovered_at=None,
        cancelled_at=None,
        version=4,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        declined_reason=AssignmentDeclineReason.VEHICLE_ISSUE,
        declined_at=datetime(2026, 9, 14, 9, 5, tzinfo=UTC),
        arrived_at=datetime(2026, 9, 14, 9, 10, tzinfo=UTC),
        scanned_at=datetime(2026, 9, 14, 9, 12, tzinfo=UTC),
        scanned_identifier="WB-1001",
        condition_proof_captured_at=datetime(2026, 9, 14, 9, 15, tzinfo=UTC),
        package_condition_status="MINOR_DAMAGE",
        exception_reason=PickupExceptionReason.PACKAGE_NOT_READY,
        exception_reported_at=datetime(2026, 9, 14, 9, 20, tzinfo=UTC),
        failed_at=None,
    )
    restored = _task_from_row(_task_to_row(entity))
    assert restored == entity
