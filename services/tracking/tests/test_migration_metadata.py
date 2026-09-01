"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from tracking.infrastructure.persistence.models import (
    Base,
    IntegrationInboxRow,
    ShipmentTimelineEntryRow,
)


def test_single_head_migration_exists() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = [path for path in versions.glob("*.py") if path.name != "__init__.py"]
    assert len(migration_files) == 1


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        IntegrationInboxRow.__tablename__,
        ShipmentTimelineEntryRow.__tablename__,
    }


def test_migration_creates_expected_tables_and_constraints() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w8a_tracking_timeline_001_initial.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert 'revision: str = "w8a_tracking_timeline_001"' in content
    assert "tracking_integration_inbox" in content
    assert "shipment_timeline_entries" in content
    assert "uq_tracking_inbox_consumer_event" in content
    assert "uq_shipment_timeline_source_row" in content
    assert "audit.fact" not in content
    assert "not a canonical Audit fact" not in content
    assert "REFERENCES" not in content.upper() or "sa.ForeignKey" not in content


def test_timeline_table_is_not_labeled_canonical_fact() -> None:
    assert ShipmentTimelineEntryRow.__tablename__ == "shipment_timeline_entries"
    assert "fact" not in ShipmentTimelineEntryRow.__tablename__
    assert IntegrationInboxRow.__table_args__[0].name == "uq_tracking_inbox_consumer_event"
