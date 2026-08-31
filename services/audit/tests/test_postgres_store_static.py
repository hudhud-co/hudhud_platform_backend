"""Static PostgreSQL adapter conformance checks."""

from __future__ import annotations

from pathlib import Path


def test_sqlalchemy_store_declares_inbox_and_projection_tables() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "audit"
        / "infrastructure"
        / "persistence"
        / "sqlalchemy_store.py"
    ).read_text(encoding="utf-8")
    assert "IntegrationInboxRow" in source
    assert "LegacyAuditObservationRow" in source
    assert "SqlAlchemyObservationQuery" in source


def test_migration_declares_inbox_and_source_row_uniqueness() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w5b_audit_observation_001_initial.py"
    ).read_text(encoding="utf-8")
    assert "uq_audit_inbox_consumer_event" in migration
    assert "uq_legacy_audit_observation_source_row" in migration
