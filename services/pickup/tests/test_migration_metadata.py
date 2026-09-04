"""Alembic migration metadata ownership tests."""

from __future__ import annotations

from pathlib import Path

from pickup.infrastructure.persistence.models import (
    AcceptanceIdempotencyRow,
    Base,
    IntegrationOutboxRow,
    PickupTaskRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
)


def test_single_head_migration_chain() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration_files = sorted(
        path for path in versions.glob("*.py") if path.name != "__init__.py"
    )
    assert len(migration_files) == 2
    heads = [
        path
        for path in migration_files
        if 'down_revision: str | Sequence[str] | None = "w15b_pickup_recovery_001"'
        in path.read_text(encoding="utf-8")
        or "down_revision: str | Sequence[str] | None = None" in path.read_text(encoding="utf-8")
    ]
    assert len(heads) == 2  # root + one child
    w17 = next(path for path in migration_files if "w17e" in path.name)
    assert 'revision: str = "w17e_pickup_accepted_outbox_001"' in w17.read_text(encoding="utf-8")


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        PickupTaskRow.__tablename__,
        RecoveryHistoryRow.__tablename__,
        RecoveryIdempotencyRow.__tablename__,
        AcceptanceIdempotencyRow.__tablename__,
        IntegrationOutboxRow.__tablename__,
    }


def test_migration_creates_expected_tables_and_constraints() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "w17e_pickup_accepted_outbox_001.py"
    )
    content = migration.read_text(encoding="utf-8")
    assert "pickup_integration_outbox" in content
    assert "pickup_acceptance_idempotency" in content
    assert "has_pickup_condition_proof" in content
    assert "accepted_at" in content
    assert "accepted_by_driver_user_id" in content
    assert "uq_pickup_outbox_event_id" in content
    assert "uq_pickup_outbox_aggregate_version" in content
    assert "uq_pickup_outbox_event_type_aggregate" in content
    assert "sa.ForeignKey" not in content


def test_outbox_row_declares_uniqueness_constraints() -> None:
    constraint_names = {
        constraint.name
        for constraint in IntegrationOutboxRow.__table_args__  # type: ignore[union-attr]
        if hasattr(constraint, "name")
    }
    assert "uq_pickup_outbox_event_id" in constraint_names
    assert "uq_pickup_outbox_aggregate_version" in constraint_names
    assert "uq_pickup_outbox_event_type_aggregate" in constraint_names


def test_pickup_task_row_declares_attempt_lineage_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in PickupTaskRow.__table_args__  # type: ignore[union-attr]
        if hasattr(constraint, "name")
    }
    assert "uq_pickup_tasks_root_attempt_number" in constraint_names
