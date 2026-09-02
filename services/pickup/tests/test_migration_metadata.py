"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from pickup.infrastructure.persistence.models import (
    Base,
    PickupTaskRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
)


def test_single_head_migration_exists() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = [path for path in versions.glob("*.py") if path.name != "__init__.py"]
    assert len(migration_files) == 1


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        PickupTaskRow.__tablename__,
        RecoveryHistoryRow.__tablename__,
        RecoveryIdempotencyRow.__tablename__,
    }


def test_migration_creates_expected_tables_and_constraints() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w15b_pickup_recovery_001_initial.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert "pickup_tasks" in content
    assert "pickup_recovery_history" in content
    assert "pickup_recovery_idempotency" in content
    assert "uq_pickup_tasks_root_attempt_number" in content
    assert "uq_pickup_recovery_history_idempotency_key" in content
    assert "sa.ForeignKey" not in content


def test_pickup_task_row_declares_attempt_lineage_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in PickupTaskRow.__table_args__  # type: ignore[union-attr]
        if hasattr(constraint, "name")
    }
    assert "uq_pickup_tasks_root_attempt_number" in constraint_names
