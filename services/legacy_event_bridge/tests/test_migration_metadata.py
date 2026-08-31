"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from legacy_event_bridge.infrastructure.persistence.models import (
    Base,
    BridgeCheckpointRow,
    BridgeLandingRow,
    BridgeOutboxRow,
)


def test_single_head_migration_chain() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = sorted(
        path for path in versions.glob("*.py") if path.name != "__init__.py"
    )
    assert len(migration_files) == 2
    assert "w5a" in "".join(path.name for path in migration_files)


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        BridgeLandingRow.__tablename__,
        BridgeCheckpointRow.__tablename__,
        BridgeOutboxRow.__tablename__,
    }


def test_migration_creates_expected_tables() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w5a_bridge_pipeline_001_initial.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert "bridge_landing" in content
    assert "bridge_checkpoint" in content
    assert "bridge_integration_outbox" in content
    assert "uq_bridge_landing_source_row" in content
    assert "uq_bridge_outbox_event_id" in content
