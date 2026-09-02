"""Shipment acceptance transaction probe executed inside the service virtualenv."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RecordAcceptanceScanCommand,
    RegisterPickupTaskCommand,
)
from shipment.domain.entities import AuditLogEntry, ShipmentEvent
from shipment.domain.errors import AcceptanceAlreadyRecorded, OptimisticConcurrencyConflict
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    EvidenceReference,
    PickupTaskAcceptanceState,
    ShipmentEventType,
    ShipmentStatus,
)
from shipment.infrastructure.persistence.acceptance_uow import SqlAlchemyAcceptanceUnitOfWork
from shipment.infrastructure.persistence.session import (
    build_async_engine,
    build_async_session_factory,
)
from sqlalchemy import create_engine, text


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    engine = build_async_engine(database_url)
    uow = SqlAlchemyAcceptanceUnitOfWork(build_async_session_factory(engine))
    service = AcceptanceLifecycleService(uow)

    now = datetime.now(UTC)
    order_id = uuid4()
    _, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=order_id,
            waybill_number="WB-PROBE-001",
            created_at=now,
        )
    )
    pickup_task_id = uuid4()
    batch_id = uuid4()
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id="driver-probe",
            assigned_batch_id=batch_id,
            has_pickup_condition_proof=True,
        )
    )

    evidence = (
        EvidenceReference.from_reference("s3://proof-lab/exception-note-001.jpg"),
        EvidenceReference.from_reference("s3://proof-lab/exception-note-002.jpg"),
    )
    acceptance = service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            pickup_task_id=pickup_task_id,
            acting_driver_user_id="driver-probe",
            scanned_identifier="WB-PROBE-001",
            scan_timestamp=now,
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            exception_evidence=evidence,
            recorded_at=now,
        )
    )
    assert acceptance.shipment.current_status is ShipmentStatus.IN_CUSTODY
    assert (
        acceptance.pickup_task.acceptance_state
        is PickupTaskAcceptanceState.ACCEPTED_WITH_EXCEPTION
    )
    assert acceptance.shipment_event is not None
    assert acceptance.audit_log.action == "SHIPMENT_ACCEPTANCE_SCAN"

    evidence_items = _fetch_exception_evidence(database_url, shipment.shipment_id)
    evidence_uri_only = all(set(item.keys()) == {"storage_uri"} for item in evidence_items)

    duplicate_rejected = False
    try:
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                pickup_task_id=pickup_task_id,
                acting_driver_user_id="driver-probe",
                scanned_identifier="WB-PROBE-001",
                scan_timestamp=now,
                outcome=AcceptanceOutcome.ACCEPTED,
            )
        )
    except AcceptanceAlreadyRecorded:
        duplicate_rejected = True

    rollback_without_partial = _probe_acceptance_rollback(database_url, now)

    stale_write_rejected = _probe_optimistic_concurrency(database_url, now)

    payload = {
        "acceptance_committed": True,
        "evidence_uri_only": evidence_uri_only,
        "duplicate_rejected": duplicate_rejected,
        "rollback_without_partial": rollback_without_partial,
        "stale_write_rejected": stale_write_rejected,
    }
    print(json.dumps(payload))
    return 0


def _sync_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


def _fetch_exception_evidence(database_url: str, shipment_id: object) -> list[dict[str, object]]:
    engine = create_engine(_sync_url(database_url), future=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT exception_evidence FROM acceptance_decisions "
                "WHERE shipment_id = :shipment_id"
            ),
            {"shipment_id": shipment_id},
        ).fetchall()
    engine.dispose()
    if not rows:
        return []
    value = rows[0][0]
    assert isinstance(value, list)
    return value


def _probe_acceptance_rollback(database_url: str, now: datetime) -> bool:
    engine = build_async_engine(database_url)
    uow = SqlAlchemyAcceptanceUnitOfWork(build_async_session_factory(engine))
    service = AcceptanceLifecycleService(uow)

    order_id = uuid4()
    _, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=order_id,
            waybill_number="WB-ROLLBACK-001",
            created_at=now,
        )
    )
    rollback_pickup_task_id = uuid4()
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=rollback_pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id="driver-rollback",
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=True,
        )
    )

    uow.begin()
    try:
        loaded_shipment = uow.shipments.get_shipment(shipment.shipment_id)
        loaded_pickup = uow.pickup_tasks.get_pickup_task(rollback_pickup_task_id)
        assert loaded_shipment is not None and loaded_pickup is not None
        loaded_pickup.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
        loaded_shipment.current_status = ShipmentStatus.IN_CUSTODY
        loaded_shipment.accepted_at = now
        loaded_shipment.sla_started_at = now
        loaded_shipment.current_custody_type = CustodyType.DRIVER
        loaded_shipment.current_custody_id = "driver-rollback"
        uow.shipment_events.append_event(
            ShipmentEvent(
                event_id=uuid4(),
                shipment_id=shipment.shipment_id,
                event_type=ShipmentEventType.ACCEPTANCE_SCAN,
                previous_status=ShipmentStatus.CREATED,
                new_status=ShipmentStatus.IN_CUSTODY,
                occurred_at=now,
            )
        )
        uow.audit_logs.append_entry(
            AuditLogEntry(
                audit_id=uuid4(),
                action="SHIPMENT_ACCEPTANCE_SCAN",
                entity_type="shipment",
                entity_id=str(shipment.shipment_id),
                actor_id="driver-rollback",
                occurred_at=now,
                details={
                    "outcome": "accepted",
                    "pickup_task_id": str(rollback_pickup_task_id),
                    "scanned_identifier": "WB-ROLLBACK-001",
                    "scan_timestamp": now.isoformat(),
                },
            )
        )
        uow.pickup_tasks.save_pickup_task(loaded_pickup)
        uow.shipments.save_shipment(loaded_shipment)
    finally:
        uow.rollback()

    sync_engine = create_engine(_sync_url(database_url), future=True)
    with sync_engine.connect() as connection:
        shipment_status = connection.execute(
            text("SELECT current_status FROM shipments WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment.shipment_id},
        ).scalar_one()
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM shipment_events WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment.shipment_id},
        ).scalar_one()
        audit_count = connection.execute(
            text("SELECT COUNT(*) FROM acceptance_audit_logs WHERE entity_id = :entity_id"),
            {"entity_id": str(shipment.shipment_id)},
        ).scalar_one()
        decision_count = connection.execute(
            text("SELECT COUNT(*) FROM acceptance_decisions WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment.shipment_id},
        ).scalar_one()
    sync_engine.dispose()

    return (
        shipment_status == ShipmentStatus.CREATED.value
        and event_count == 0
        and audit_count == 0
        and decision_count == 0
    )


def _probe_optimistic_concurrency(database_url: str, now: datetime) -> bool:
    engine = build_async_engine(database_url)
    uow = SqlAlchemyAcceptanceUnitOfWork(build_async_session_factory(engine))
    service = AcceptanceLifecycleService(uow)

    order_id = uuid4()
    _, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=order_id,
            waybill_number="WB-STALE-001",
            created_at=now,
        )
    )

    uow.begin()
    try:
        loaded = uow.shipments.get_shipment(shipment.shipment_id)
        assert loaded is not None

        sync_engine = create_engine(_sync_url(database_url), future=True)
        with sync_engine.connect() as connection:
            connection.execute(
                text(
                    "UPDATE shipments SET version = version + 1 "
                    "WHERE shipment_id = :shipment_id"
                ),
                {"shipment_id": shipment.shipment_id},
            )
            connection.commit()
        sync_engine.dispose()

        loaded.current_custody_id = "stale-writer"
        uow.shipments.save_shipment(loaded)
        uow.commit()
    except OptimisticConcurrencyConflict:
        uow.rollback()
        return True
    else:
        uow.rollback()
        return False


if __name__ == "__main__":
    raise SystemExit(main())
