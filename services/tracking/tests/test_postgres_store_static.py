"""Static PostgreSQL adapter conformance checks."""

from __future__ import annotations

from pathlib import Path


def test_sqlalchemy_store_declares_inbox_and_projection_tables() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "tracking"
        / "infrastructure"
        / "persistence"
        / "sqlalchemy_store.py"
    ).read_text(encoding="utf-8")
    assert "IntegrationInboxRow" in source
    assert "ShipmentTimelineEntryRow" in source
    assert "SqlAlchemyTimelineQuery" in source


def test_migration_declares_inbox_and_source_row_uniqueness() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w8a_tracking_timeline_001_initial.py"
    ).read_text(encoding="utf-8")
    assert "uq_tracking_inbox_consumer_event" in migration
    assert "uq_shipment_timeline_source_row" in migration
    assert "ix_shipment_timeline_shipment_occurred" in migration
