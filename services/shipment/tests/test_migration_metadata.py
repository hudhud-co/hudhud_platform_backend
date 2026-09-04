"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from shipment.infrastructure.persistence.models import (
    AcceptanceAuditLogRow,
    AcceptanceDecisionRow,
    AcceptanceIdempotencyRow,
    Base,
    OrderIntentRow,
    PickupTaskSnapshotRow,
    ShipmentEventRow,
    ShipmentRow,
)


def test_single_head_migration_chain() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = sorted(
        path.name for path in versions.glob("*.py") if path.name != "__init__.py"
    )
    assert migration_files == [
        "w15a_shipment_acceptance_001_initial.py",
        "w16a_shipment_acceptance_idempotency_001.py",
    ]
    w16 = (versions / "w16a_shipment_acceptance_idempotency_001.py").read_text(encoding="utf-8")
    assert "down_revision" in w16
    assert "w15a_shipment_acceptance_001" in w16
    assert 'revision: str = "w16a_acceptance_idempotency_001"' in w16
    assert len("w16a_acceptance_idempotency_001") <= 32


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        OrderIntentRow.__tablename__,
        ShipmentRow.__tablename__,
        PickupTaskSnapshotRow.__tablename__,
        ShipmentEventRow.__tablename__,
        AcceptanceAuditLogRow.__tablename__,
        AcceptanceDecisionRow.__tablename__,
        AcceptanceIdempotencyRow.__tablename__,
    }


def test_migration_declares_acceptance_idempotency_constraints() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w15a_shipment_acceptance_001_initial.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert "uq_acceptance_decisions_shipment_id" in content
    assert "uq_acceptance_decisions_pickup_task_id" in content
    assert "uq_shipment_events_shipment_event_type" in content
    assert "uq_shipments_waybill_number" in content
    assert "version" in content
    assert "sa.ForeignKey" not in content
