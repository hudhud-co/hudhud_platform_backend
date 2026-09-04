"""Focused unit tests for PostgreSQL acceptance persistence adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from shipment.domain.entities import AuditLogEntry, PickupTaskSnapshot, Shipment
from shipment.domain.value_objects import (
    CustodyType,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.infrastructure.persistence.mappers import (
    acceptance_decision_from_audit,
    pickup_task_from_row,
    pickup_task_to_row,
    shipment_from_row,
    shipment_to_row,
)
from shipment.infrastructure.persistence.models import ShipmentRow
from shipment.infrastructure.persistence.session import build_async_session_factory


def test_async_session_factory_is_exported() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "shipment"
        / "infrastructure"
        / "persistence"
        / "session.py"
    ).read_text(encoding="utf-8")
    assert "build_async_session_factory" in source
    assert "create_async_engine" in source


def test_acceptance_uow_declares_transaction_boundary() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "shipment"
        / "infrastructure"
        / "persistence"
        / "acceptance_uow.py"
    ).read_text(encoding="utf-8")
    assert "SqlAlchemyAcceptanceUnitOfWork" in source
    assert "OptimisticConcurrencyConflict" in source
    assert "AcceptanceAlreadyRecorded" in source
    assert "async def begin" in source
    assert "run_until_complete" not in source
    assert "_run_async" not in source
    assert "asyncio.run" not in source


def test_shipment_mapper_round_trip_preserves_aggregate_fields() -> None:
    shipment_id = uuid4()
    order_id = uuid4()
    accepted_at = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
    shipment = Shipment(
        shipment_id=shipment_id,
        order_id=order_id,
        waybill_identity=WaybillIdentity(waybill_number="WB-1001", shipment_id=str(shipment_id)),
        current_status=ShipmentStatus.IN_CUSTODY,
        order_created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        accepted_at=accepted_at,
        sla_started_at=accepted_at,
        current_custody_type=CustodyType.PICKUP_DRIVER,
        current_custody_id="driver-42",
    )
    row = shipment_to_row(shipment, version=3)
    assert row.current_custody_type == "PICKUP_DRIVER"
    restored, version = shipment_from_row(row)
    assert version == 3
    assert restored == shipment


def test_shipment_mapper_rejects_ambiguous_driver_custody_type() -> None:
    """Persisted bootstrap value DRIVER must not be silently remapped on read."""
    shipment_id = uuid4()
    row = ShipmentRow(
        shipment_id=shipment_id,
        order_id=uuid4(),
        waybill_number="WB-LEGACY-DRIVER",
        current_status=ShipmentStatus.IN_CUSTODY.value,
        order_created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        accepted_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
        sla_started_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
        current_custody_type="DRIVER",
        current_custody_id="driver-42",
        version=1,
    )
    with pytest.raises(ValueError):
        shipment_from_row(row)


def test_acceptance_decision_stores_external_evidence_refs_only() -> None:
    shipment_id = uuid4()
    pickup_task_id = uuid4()
    scan_timestamp = datetime(2026, 5, 31, 10, 0, tzinfo=UTC)
    entry = AuditLogEntry(
        audit_id=uuid4(),
        action="SHIPMENT_ACCEPTANCE_SCAN",
        entity_type="shipment",
        entity_id=str(shipment_id),
        actor_id="driver-42",
        occurred_at=scan_timestamp,
        details={
            "outcome": "accepted_with_exception",
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-1001",
            "scan_timestamp": scan_timestamp.isoformat(),
            "exception_evidence_uris": (
                "s3://proof-bucket/exception-note-001.jpg,"
                "s3://proof-bucket/exception-note-002.jpg"
            ),
        },
    )
    decision = acceptance_decision_from_audit(entry)
    assert decision.shipment_id == shipment_id
    assert decision.pickup_task_id == pickup_task_id
    assert decision.exception_evidence == [
        {"storage_uri": "s3://proof-bucket/exception-note-001.jpg"},
        {"storage_uri": "s3://proof-bucket/exception-note-002.jpg"},
    ]
    assert all("storage_uri" in item for item in decision.exception_evidence)


def test_shipment_row_tracks_version_for_optimistic_concurrency() -> None:
    columns = {column.name for column in ShipmentRow.__table__.columns}
    assert "version" in columns


def test_pickup_task_snapshot_row_tracks_acceptance_state() -> None:
    pickup_task = PickupTaskSnapshot(
        pickup_task_id=uuid4(),
        shipment_id=uuid4(),
        status=PickupTaskStatus.PROOF_CAPTURED,
        assigned_driver_user_id="driver-42",
        assigned_batch_id=uuid4(),
        has_pickup_condition_proof=True,
        acceptance_state=PickupTaskAcceptanceState.ACCEPTED,
    )
    row = pickup_task_to_row(pickup_task, version=2)
    restored, version = pickup_task_from_row(row)
    assert version == 2
    assert restored.acceptance_state is PickupTaskAcceptanceState.ACCEPTED


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://localhost/shipment",
    ],
)
def test_build_async_session_factory_accepts_postgres_url(database_url: str) -> None:
    pytest.importorskip("asyncpg")
    engine = create_async_engine(database_url)
    factory = build_async_session_factory(engine)
    assert factory.kw.get("expire_on_commit") is False
