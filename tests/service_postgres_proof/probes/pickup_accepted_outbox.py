"""Pickup acceptance + transactional outbox probe executed inside the service virtualenv."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.recovery_service import (
    PickupRecoveryService,
    RegisterPickupTaskCommand,
)
from pickup.domain.errors import AcceptanceOutcomeNotAllowed, ConflictingIdempotencyKey
from pickup.domain.value_objects import (
    AcceptanceOutcome,
    EvidenceMediaRef,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentStatus,
)
from pickup.infrastructure.contracts.registry import validate_accepted_fact_envelope
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyPickupUnitOfWork
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
    uow = SqlAlchemyPickupUnitOfWork(build_session_factory(engine))
    recovery = PickupRecoveryService(uow, eligibility)
    service = PickupAcceptanceService(uow)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    task_id = _seed_ready_task(recovery, eligibility, now, driver_id="driver-outbox")
    first = service.accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task_id,
            acting_driver_user_id="driver-outbox",
            scanned_identifier="WB-OUTBOX-001",
            outcome=AcceptanceOutcome.ACCEPTED,
            idempotency_key="probe-accept-001",
            accepted_at=now,
        )
    )
    task = uow.pickup_tasks.get_pickup_task(task_id)
    assert task is not None
    assert task.acceptance_state is PickupTaskAcceptanceState.ACCEPTED
    assert task.version == first.aggregate_version == 2
    assert first.outbox_record.aggregate_version == task.version
    assert first.outbox_record.payload_json["aggregate_version"] == task.version
    validate_accepted_fact_envelope(first.outbox_record.payload_json)
    envelope_valid = True
    atomic_commit = (
        _count(database_url, "pickup_tasks") >= 1
        and _count(database_url, "pickup_integration_outbox") >= 1
        and _count(database_url, "pickup_acceptance_idempotency") >= 1
    )

    replay = service.accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task_id,
            acting_driver_user_id="driver-outbox",
            scanned_identifier="WB-OUTBOX-001",
            outcome=AcceptanceOutcome.ACCEPTED,
            idempotency_key="probe-accept-001",
            accepted_at=now,
        )
    )
    outbox_after_replay = _count(
        database_url,
        "pickup_integration_outbox",
        "WHERE aggregate_id = :aggregate_id",
        {"aggregate_id": task_id},
    )
    idempotent_same_event = replay.idempotent_replay is True and replay.event_id == first.event_id
    no_second_row = outbox_after_replay == 1

    conflicting_key_no_mutation = False
    before_conflict_outbox = outbox_after_replay
    before_conflict_version = task.version
    try:
        service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task_id,
                acting_driver_user_id="driver-outbox",
                scanned_identifier="WB-OUTBOX-OTHER",
                outcome=AcceptanceOutcome.ACCEPTED,
                idempotency_key="probe-accept-001",
                accepted_at=now,
            )
        )
    except ConflictingIdempotencyKey:
        after_task = uow.pickup_tasks.get_pickup_task(task_id)
        after_outbox = _count(
            database_url,
            "pickup_integration_outbox",
            "WHERE aggregate_id = :aggregate_id",
            {"aggregate_id": task_id},
        )
        conflicting_key_no_mutation = (
            after_task is not None
            and after_task.version == before_conflict_version
            and after_outbox == before_conflict_outbox
        )

    uniqueness_rejected = _probe_outbox_uniqueness(database_url, first)
    rollback_unchanged = _probe_commit_failure(database_url, eligibility, now)
    exception_media_refs = _probe_exception_media_refs(recovery, eligibility, service, now)
    rejected_creates_no_fact = _probe_rejected_creates_no_fact(
        recovery, eligibility, service, database_url, now
    )

    payload = {
        "atomic_commit": atomic_commit,
        "version_agrees": first.aggregate_version == 2,
        "envelope_valid": envelope_valid,
        "idempotent_same_event": idempotent_same_event,
        "no_second_row": no_second_row,
        "conflicting_key_no_mutation": conflicting_key_no_mutation,
        "rollback_unchanged": rollback_unchanged,
        "uniqueness_rejected": uniqueness_rejected,
        "exception_media_refs": exception_media_refs,
        "rejected_creates_no_fact": rejected_creates_no_fact,
    }
    print(json.dumps(payload))
    return 0


def _seed_ready_task(
    recovery: PickupRecoveryService,
    eligibility: InMemoryShipmentEligibilityAdapter,
    now: datetime,
    *,
    driver_id: str,
) -> object:
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
    task_id = uuid4()
    recovery.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=True,
            status=PickupTaskStatus.PROOF_CAPTURED,
            created_at=now,
        )
    )
    return task_id


def _count(
    database_url: str,
    table: str,
    where: str = "",
    params: dict[str, object] | None = None,
) -> int:
    engine = create_engine(database_url, future=True)
    with engine.connect() as connection:
        count = connection.execute(
            text(f"SELECT COUNT(*) FROM {table} {where}"),
            params or {},
        ).scalar_one()
    engine.dispose()
    return int(count)


def _probe_outbox_uniqueness(database_url: str, first: object) -> bool:
    engine = create_engine(database_url, future=True)
    duplicate_event = False
    duplicate_version = False
    duplicate_type_aggregate = False
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pickup_integration_outbox ("
                    "id, event_id, subject, event_type, event_version, aggregate_id, "
                    "aggregate_version, payload_json, status, attempt_count, max_attempts, "
                    "next_attempt_at, created_at"
                    ") VALUES ("
                    ":id, :event_id, 'hudhud.pickup.pickup.fact.accepted.v1', "
                    "'pickup.fact.accepted', 1, :aggregate_id, 99, "
                    "CAST(:payload AS jsonb), 'pending', 0, 5, :now, :now)"
                ),
                {
                    "id": uuid4(),
                    "event_id": first.event_id,  # type: ignore[attr-defined]
                    "aggregate_id": uuid4(),
                    "payload": "{}",
                    "now": now,
                },
            )
    except IntegrityError:
        duplicate_event = True
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pickup_integration_outbox ("
                    "id, event_id, subject, event_type, event_version, aggregate_id, "
                    "aggregate_version, payload_json, status, attempt_count, max_attempts, "
                    "next_attempt_at, created_at"
                    ") VALUES ("
                    ":id, :event_id, 'hudhud.pickup.pickup.fact.accepted.v1', "
                    "'pickup.fact.accepted', 1, :aggregate_id, :aggregate_version, "
                    "CAST(:payload AS jsonb), 'pending', 0, 5, :now, :now)"
                ),
                {
                    "id": uuid4(),
                    "event_id": uuid4(),
                    "aggregate_id": first.pickup_task.pickup_task_id,  # type: ignore[attr-defined]
                    "aggregate_version": first.aggregate_version,  # type: ignore[attr-defined]
                    "payload": "{}",
                    "now": now,
                },
            )
    except IntegrityError:
        duplicate_version = True
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pickup_integration_outbox ("
                    "id, event_id, subject, event_type, event_version, aggregate_id, "
                    "aggregate_version, payload_json, status, attempt_count, max_attempts, "
                    "next_attempt_at, created_at"
                    ") VALUES ("
                    ":id, :event_id, 'hudhud.pickup.pickup.fact.accepted.v1', "
                    "'pickup.fact.accepted', 1, :aggregate_id, 50, "
                    "CAST(:payload AS jsonb), 'pending', 0, 5, :now, :now)"
                ),
                {
                    "id": uuid4(),
                    "event_id": uuid4(),
                    "aggregate_id": first.pickup_task.pickup_task_id,  # type: ignore[attr-defined]
                    "payload": "{}",
                    "now": now,
                },
            )
    except IntegrityError:
        duplicate_type_aggregate = True
    engine.dispose()
    return duplicate_event and duplicate_version and duplicate_type_aggregate


def _probe_commit_failure(
    database_url: str,
    eligibility: InMemoryShipmentEligibilityAdapter,
    now: datetime,
) -> bool:
    engine = build_engine(database_url)
    factory = build_session_factory(engine)

    class _FailingUow(SqlAlchemyPickupUnitOfWork):
        def commit(self) -> None:  # type: ignore[override]
            self.rollback()
            raise RuntimeError("simulated commit failure")

    uow = _FailingUow(factory)
    recovery = PickupRecoveryService(uow, eligibility)
    service = PickupAcceptanceService(uow)
    task_id = _seed_ready_task(recovery, eligibility, now, driver_id="driver-rollback")
    before_outbox = _count(database_url, "pickup_integration_outbox")
    before_idempotency = _count(database_url, "pickup_acceptance_idempotency")
    failed = False
    try:
        service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task_id,
                acting_driver_user_id="driver-rollback",
                scanned_identifier="WB-ROLLBACK",
                outcome=AcceptanceOutcome.ACCEPTED,
                idempotency_key="probe-rollback-accept",
                accepted_at=now,
            )
        )
    except RuntimeError:
        failed = True
    task = SqlAlchemyPickupUnitOfWork(factory).pickup_tasks.get_pickup_task(task_id)
    after_outbox = _count(database_url, "pickup_integration_outbox")
    after_idempotency = _count(database_url, "pickup_acceptance_idempotency")
    return (
        failed
        and task is not None
        and task.acceptance_state is None
        and task.version == 1
        and before_outbox == after_outbox
        and before_idempotency == after_idempotency
    )


def _probe_exception_media_refs(
    recovery: PickupRecoveryService,
    eligibility: InMemoryShipmentEligibilityAdapter,
    service: PickupAcceptanceService,
    now: datetime,
) -> bool:
    task_id = _seed_ready_task(recovery, eligibility, now, driver_id="driver-exception")
    media = (
        EvidenceMediaRef(
            ref_type="s3",
            bucket="hudhud-evidence",
            key=f"pickup-evidence/{task_id}/exception-note.jpg",
            content_type="image/jpeg",
        ),
    )
    result = service.accept_pickup_task(
        AcceptPickupTaskCommand(
            pickup_task_id=task_id,
            acting_driver_user_id="driver-exception",
            scanned_identifier="WB-EXC-001",
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            idempotency_key="probe-exception-001",
            accepted_at=now,
            media_refs=media,
        )
    )
    refs = result.outbox_record.payload_json.get("media_refs") or []
    payload = result.outbox_record.payload_json.get("payload") or {}
    return (
        result.pickup_task.acceptance_state is PickupTaskAcceptanceState.ACCEPTED_WITH_EXCEPTION
        and len(refs) == 1
        and str(refs[0].get("key", "")).endswith("exception-note.jpg")
        and "exception_evidence" not in payload
        and "inline_evidence" not in payload
    )


def _probe_rejected_creates_no_fact(
    recovery: PickupRecoveryService,
    eligibility: InMemoryShipmentEligibilityAdapter,
    service: PickupAcceptanceService,
    database_url: str,
    now: datetime,
) -> bool:
    task_id = _seed_ready_task(recovery, eligibility, now, driver_id="driver-rejected")
    before = _count(
        database_url,
        "pickup_integration_outbox",
        "WHERE aggregate_id = :aggregate_id",
        {"aggregate_id": task_id},
    )
    rejected = False
    try:
        service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task_id,
                acting_driver_user_id="driver-rejected",
                scanned_identifier="WB-REJ-001",
                outcome="REJECTED",
                idempotency_key="probe-rejected-001",
                accepted_at=now,
            )
        )
    except AcceptanceOutcomeNotAllowed:
        rejected = True
    after = _count(
        database_url,
        "pickup_integration_outbox",
        "WHERE aggregate_id = :aggregate_id",
        {"aggregate_id": task_id},
    )
    engine = build_engine(database_url)
    task = SqlAlchemyPickupUnitOfWork(
        build_session_factory(engine)
    ).pickup_tasks.get_pickup_task(task_id)
    return (
        rejected
        and before == after == 0
        and task is not None
        and task.acceptance_state is None
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — probe entrypoint reports failure
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

