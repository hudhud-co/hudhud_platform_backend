"""Pickup recovery transaction probe executed inside the service virtualenv."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from pickup.application.recovery_service import (
    PickupRecoveryService,
    RecoveryCommand,
    RegisterPickupTaskCommand,
)
from pickup.domain.entities import IdempotencyRecord, PickupTask, RecoveryHistoryEntry
from pickup.domain.errors import StalePickupTaskVersion
from pickup.domain.value_objects import PickupTaskStatus, RecoveryAction, ShipmentStatus
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyRecoveryUnitOfWork
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = build_engine(database_url)
    eligibility = InMemoryShipmentEligibilityAdapter()
    uow = SqlAlchemyRecoveryUnitOfWork(build_session_factory(engine))
    service = PickupRecoveryService(uow, eligibility)

    now = datetime.now(UTC)
    shipment_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )

    pickup_task_id = uuid4()
    batch_id = uuid4()
    task = service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-probe",
            assigned_batch_id=batch_id,
            created_at=now,
        )
    )
    assert task.version == 1

    idempotency_key = "probe-recovery-retry-001"
    result = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=pickup_task_id,
            idempotency_key=idempotency_key,
            reason="customer unavailable",
            occurred_at=now,
        )
    )
    assert result.replacement_task is not None
    assert result.original_task.status is PickupTaskStatus.SUPERSEDED
    assert result.replacement_task.attempt_number == 2
    assert result.replacement_task.parent_attempt_id == pickup_task_id

    replay = service.retry_pickup(
        RecoveryCommand(
            pickup_task_id=pickup_task_id,
            idempotency_key=idempotency_key,
            reason="customer unavailable",
            occurred_at=now,
        )
    )
    assert replay.idempotent_replay is True

    task_count_after_replay = _count_rows(database_url, "pickup_tasks")
    history_count = _count_rows(database_url, "pickup_recovery_history")
    idempotency_count = _count_rows(database_url, "pickup_recovery_idempotency")

    stale_write_rejected = False
    replacement_id = result.replacement_task.pickup_task_id
    try:
        service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=replacement_id,
                idempotency_key="probe-stale-version",
                reason="retry again",
                occurred_at=now,
                expected_version=999,
            )
        )
    except StalePickupTaskVersion:
        stale_write_rejected = True

    lineage_duplicate_blocked = _probe_lineage_uniqueness(database_url, shipment_id, now)

    rollback_without_partial = _probe_recovery_rollback(database_url, eligibility, now)

    payload = {
        "recovery_committed": True,
        "idempotent_replay": replay.idempotent_replay,
        "task_count_after_replay": task_count_after_replay,
        "history_count": history_count,
        "idempotency_count": idempotency_count,
        "stale_write_rejected": stale_write_rejected,
        "lineage_duplicate_blocked": lineage_duplicate_blocked,
        "rollback_without_partial": rollback_without_partial,
    }
    print(json.dumps(payload))
    return 0


def _count_rows(database_url: str, table: str) -> int:
    engine = create_engine(database_url, future=True)
    with engine.connect() as connection:
        count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    engine.dispose()
    return int(count)


def _probe_lineage_uniqueness(database_url: str, shipment_id: object, now: datetime) -> bool:
    engine = build_engine(database_url)
    eligibility = InMemoryShipmentEligibilityAdapter()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,  # type: ignore[arg-type]
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    uow = SqlAlchemyRecoveryUnitOfWork(build_session_factory(engine))
    service = PickupRecoveryService(uow, eligibility)

    root_id = uuid4()
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=root_id,
            shipment_id=shipment_id,  # type: ignore[arg-type]
            assigned_driver_user_id="driver-lineage",
            assigned_batch_id=uuid4(),
            created_at=now,
        )
    )

    sync_engine = create_engine(database_url, future=True)
    duplicate_blocked = False
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pickup_tasks ("
                    "pickup_task_id, shipment_id, assigned_driver_user_id, assigned_batch_id, "
                    "status, attempt_number, root_attempt_id, parent_attempt_id, "
                    "superseded_by_task_id, scheduled_window_start, scheduled_window_end, "
                    "acceptance_state, has_pickup_condition_proof, accepted_at, "
                    "accepted_by_driver_user_id, recovery_reason, created_at, recovered_at, "
                    "cancelled_at, version"
                    ") VALUES ("
                    ":pickup_task_id, :shipment_id, 'driver-lineage', :assigned_batch_id, "
                    "'PENDING', 1, :root_attempt_id, NULL, "
                    "NULL, NULL, NULL, NULL, false, NULL, NULL, NULL, :created_at, NULL, NULL, 1"
                    ")"
                ),
                {
                    "pickup_task_id": uuid4(),
                    "shipment_id": shipment_id,
                    "assigned_batch_id": uuid4(),
                    "root_attempt_id": root_id,
                    "created_at": now,
                },
            )
    except IntegrityError:
        duplicate_blocked = True
    sync_engine.dispose()
    return duplicate_blocked


def _probe_recovery_rollback(
    database_url: str,
    eligibility: InMemoryShipmentEligibilityAdapter,
    now: datetime,
) -> bool:
    engine = build_engine(database_url)
    uow = SqlAlchemyRecoveryUnitOfWork(build_session_factory(engine))
    service = PickupRecoveryService(uow, eligibility)

    shipment_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    original_id = uuid4()
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=original_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-rollback",
            assigned_batch_id=uuid4(),
            created_at=now,
        )
    )

    before_tasks = _count_rows(database_url, "pickup_tasks")
    before_history = _count_rows(database_url, "pickup_recovery_history")
    before_idempotency = _count_rows(database_url, "pickup_recovery_idempotency")

    replacement_id = uuid4()
    uow.begin()
    try:
        original = uow.pickup_tasks.get_pickup_task(original_id)
        assert original is not None
        original.status = PickupTaskStatus.SUPERSEDED
        original.superseded_by_task_id = replacement_id
        original.version += 1
        uow.pickup_tasks.save_pickup_task(original)

        replacement = PickupTask(
            pickup_task_id=replacement_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-rollback",
            assigned_batch_id=original.assigned_batch_id,
            status=PickupTaskStatus.PENDING,
            attempt_number=2,
            root_attempt_id=original.root_attempt_id,
            parent_attempt_id=original_id,
            superseded_by_task_id=None,
            scheduled_window_start=None,
            scheduled_window_end=None,
            acceptance_state=None,
            has_pickup_condition_proof=False,
            accepted_at=None,
            accepted_by_driver_user_id=None,
            recovery_reason="rollback probe",
            created_at=now,
            recovered_at=None,
            cancelled_at=None,
            version=1,
        )
        uow.pickup_tasks.save_pickup_task(replacement)
        uow.recovery_history.append_entry(
            RecoveryHistoryEntry(
                history_id=uuid4(),
                pickup_task_id=original_id,
                replacement_task_id=replacement_id,
                action=RecoveryAction.RETRY,
                reason="rollback probe",
                idempotency_key="rollback-probe-key",
                occurred_at=now,
            )
        )
        uow.idempotency.save_record(
            IdempotencyRecord(
                idempotency_key="rollback-probe-key",
                command_fingerprint="rollback-probe-fingerprint",
                pickup_task_id=original_id,
                action=RecoveryAction.RETRY,
                original_task_id=original_id,
                result_task_id=replacement_id,
                recorded_at=now,
            )
        )
    finally:
        uow.rollback()

    after_tasks = _count_rows(database_url, "pickup_tasks")
    after_history = _count_rows(database_url, "pickup_recovery_history")
    after_idempotency = _count_rows(database_url, "pickup_recovery_idempotency")

    status = _scalar(
        database_url,
        "SELECT status FROM pickup_tasks WHERE pickup_task_id = :pickup_task_id",
        {"pickup_task_id": original_id},
    )
    return (
        before_tasks == after_tasks
        and before_history == after_history
        and before_idempotency == after_idempotency
        and status == PickupTaskStatus.PROOF_CAPTURED.value
    )


def _scalar(database_url: str, sql: str, params: dict[str, object]) -> object:
    engine = create_engine(database_url, future=True)
    with engine.connect() as connection:
        value = connection.execute(text(sql), params).scalar_one()
    engine.dispose()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
