"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from audit.infrastructure.persistence.models import (
    Base,
    IntegrationInboxRow,
    LegacyAuditObservationRow,
)


def test_single_head_migration_exists() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = [path for path in versions.glob("*.py") if path.name != "__init__.py"]
    assert len(migration_files) == 1


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        IntegrationInboxRow.__tablename__,
        LegacyAuditObservationRow.__tablename__,
    }


def test_migration_creates_expected_tables_and_constraints() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w5b_audit_observation_001_initial.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert "audit_integration_inbox" in content
    assert "legacy_audit_observations" in content
    assert "uq_audit_inbox_consumer_event" in content
    assert "uq_legacy_audit_observation_source_row" in content
    assert "audit.fact" not in content
    assert "not a canonical Audit fact" in content
    assert "REFERENCES" not in content.upper() or "sa.ForeignKey" not in content


def test_observation_table_is_not_labeled_canonical_fact() -> None:
    assert LegacyAuditObservationRow.__tablename__ == "legacy_audit_observations"
    assert "fact" not in LegacyAuditObservationRow.__tablename__
    assert IntegrationInboxRow.__table_args__[0].name == "uq_audit_inbox_consumer_event"
