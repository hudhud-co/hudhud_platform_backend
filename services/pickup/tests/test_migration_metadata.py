"""Alembic migration metadata ownership tests."""

from __future__ import annotations

import re
from pathlib import Path

from pickup.infrastructure.persistence.models import (
    AcceptanceIdempotencyRow,
    Base,
    CourierChallengeRow,
    CourierManifestRow,
    DriverWorkSessionRow,
    HandoverManifestItemRow,
    HandoverManifestRow,
    IntegrationOutboxRow,
    OfflineAuthorizationRow,
    OfflineEventRow,
    OfflineStreamRow,
    PickupTaskRow,
    ReconciliationCaseRow,
    RecoveryHistoryRow,
    RecoveryIdempotencyRow,
    TaskHistoryRow,
)


def _migration_files() -> list[Path]:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    return sorted(path for path in versions.glob("*.py") if path.name != "__init__.py")


def test_single_head_migration_chain() -> None:
    """Exactly one root and one head — no branch, no duplicate down_revision."""
    revisions: dict[str, str | None] = {}
    for path in _migration_files():
        content = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([^"]+)"', content, re.MULTILINE)
        down = re.search(
            r"^down_revision: str \| Sequence\[str\] \| None = (?:\"([^\"]+)\"|None)",
            content,
            re.MULTILINE,
        )
        assert revision is not None, path.name
        assert down is not None, path.name
        revisions[revision.group(1)] = down.group(1)

    roots = [rev for rev, down in revisions.items() if down is None]
    assert roots == ["w15b_pickup_recovery_001"]
    parents = {down for down in revisions.values() if down is not None}
    assert len(parents) == len(revisions) - 1  # no two migrations share a parent
    heads = [rev for rev in revisions if rev not in parents]
    assert heads == ["w19c_pickup_stop_outcomes_001"]


def test_metadata_tables_owned_by_service() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        PickupTaskRow.__tablename__,
        RecoveryHistoryRow.__tablename__,
        RecoveryIdempotencyRow.__tablename__,
        AcceptanceIdempotencyRow.__tablename__,
        IntegrationOutboxRow.__tablename__,
        TaskHistoryRow.__tablename__,
        DriverWorkSessionRow.__tablename__,
        CourierChallengeRow.__tablename__,
        CourierManifestRow.__tablename__,
        HandoverManifestRow.__tablename__,
        HandoverManifestItemRow.__tablename__,
        OfflineAuthorizationRow.__tablename__,
        OfflineStreamRow.__tablename__,
        OfflineEventRow.__tablename__,
        ReconciliationCaseRow.__tablename__,
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


def test_pickup_task_row_indexes_the_driver_workload_query() -> None:
    index_names = {
        item.name
        for item in PickupTaskRow.__table_args__  # type: ignore[union-attr]
        if hasattr(item, "name")
    }
    assert "ix_pickup_tasks_driver_status" in index_names


def test_pickup_task_row_declares_attempt_lineage_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in PickupTaskRow.__table_args__  # type: ignore[union-attr]
        if hasattr(constraint, "name")
    }
    assert "uq_pickup_tasks_root_attempt_number" in constraint_names
