"""PostgreSQL integration proof for Pickup migrations and recovery persistence."""

from __future__ import annotations

import pytest

from .constants import (
    PICKUP_DATABASE,
    PICKUP_EXPECTED_HEAD,
    PICKUP_ROLE,
    PICKUP_TABLES,
    SHIPMENT_DATABASE,
)
from .helpers import (
    PICKUP_SERVICE,
    alembic_current_revision,
    alembic_upgrade_head,
    assert_single_head,
    compose_restart_postgres,
    fetch_foreign_key_count,
    fetch_indexes,
    fetch_timestamp_columns,
    fetch_unique_constraints,
    grant_service_role_privileges,
    list_public_tables,
    pickup_owner_url,
    pickup_service_url,
    psql,
    psql_expect_failure,
    run_pickup_transaction_probe,
    table_count,
)

pytestmark = pytest.mark.integration


def test_pickup_starts_from_empty_database(postgres_proof_stack: int) -> None:
    _ = postgres_proof_stack
    tables = list_public_tables(PICKUP_DATABASE)
    assert tables == set()
    version = psql(
        "SELECT to_regclass('public.alembic_version');",
        database=PICKUP_DATABASE,
    )
    assert version == ""


def test_pickup_alembic_upgrade_head_and_single_head(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    assert_single_head(PICKUP_SERVICE, owner_url, PICKUP_EXPECTED_HEAD)
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)
    assert list_public_tables(PICKUP_DATABASE) == PICKUP_TABLES
    assert alembic_current_revision(PICKUP_SERVICE, owner_url) == PICKUP_EXPECTED_HEAD


def test_pickup_schema_constraints_and_types(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)

    task_timestamps = fetch_timestamp_columns(PICKUP_DATABASE, "pickup_tasks")
    assert {"created_at", "recovered_at", "cancelled_at"}.issubset(task_timestamps)
    history_timestamps = fetch_timestamp_columns(PICKUP_DATABASE, "pickup_recovery_history")
    assert "occurred_at" in history_timestamps
    idempotency_timestamps = fetch_timestamp_columns(PICKUP_DATABASE, "pickup_recovery_idempotency")
    assert "recorded_at" in idempotency_timestamps

    uniques = fetch_unique_constraints(PICKUP_DATABASE)
    assert "uq_pickup_tasks_root_attempt_number" in uniques
    assert "uq_pickup_recovery_history_idempotency_key" in uniques

    indexes = fetch_indexes(PICKUP_DATABASE)
    assert "ix_pickup_tasks_shipment_id" in indexes
    assert "ix_pickup_tasks_root_attempt_id" in indexes
    assert "ix_pickup_recovery_history_pickup_task_id" in indexes

    assert fetch_foreign_key_count(PICKUP_DATABASE) == 0


def test_pickup_service_role_isolated_from_shipment_database(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)
    grant_service_role_privileges(database=PICKUP_DATABASE, role=PICKUP_ROLE)

    psql_expect_failure(
        "SELECT 1;",
        user=PICKUP_ROLE,
        database=SHIPMENT_DATABASE,
    )


def test_pickup_upgrade_head_is_idempotent(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)
    before_tables = {table: table_count(PICKUP_DATABASE, table) for table in PICKUP_TABLES}
    before_revision = alembic_current_revision(PICKUP_SERVICE, owner_url)

    alembic_upgrade_head(PICKUP_SERVICE, owner_url)

    after_tables = {table: table_count(PICKUP_DATABASE, table) for table in PICKUP_TABLES}
    after_revision = alembic_current_revision(PICKUP_SERVICE, owner_url)
    assert before_tables == after_tables
    assert before_revision == after_revision == PICKUP_EXPECTED_HEAD


def test_pickup_schema_persists_after_postgres_restart(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)
    compose_restart_postgres()
    assert list_public_tables(PICKUP_DATABASE) == PICKUP_TABLES
    assert alembic_current_revision(PICKUP_SERVICE, owner_url) == PICKUP_EXPECTED_HEAD


def test_pickup_recovery_transactions(postgres_proof_stack: int) -> None:
    owner_url = pickup_owner_url()
    alembic_upgrade_head(PICKUP_SERVICE, owner_url)
    grant_service_role_privileges(database=PICKUP_DATABASE, role=PICKUP_ROLE)

    result = run_pickup_transaction_probe(pickup_service_url())
    assert result["recovery_committed"] is True
    assert result["idempotent_replay"] is True
    assert result["task_count_after_replay"] == 2
    assert result["history_count"] == 1
    assert result["idempotency_count"] == 1
    assert result["stale_write_rejected"] is True
    assert result["lineage_duplicate_blocked"] is True
    assert result["rollback_without_partial"] is True
