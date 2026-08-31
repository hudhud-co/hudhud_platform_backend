"""PostgreSQL integration proof for Audit service migrations and persistence."""

from __future__ import annotations

import pytest

from .constants import (
    AUDIT_DATABASE,
    AUDIT_EXPECTED_HEAD,
    AUDIT_ROLE,
    AUDIT_TABLES,
    BRIDGE_DATABASE,
)
from .helpers import (
    AUDIT_SERVICE,
    alembic_current_revision,
    alembic_upgrade_head,
    assert_single_head,
    audit_owner_url,
    audit_service_url,
    compose_restart_postgres,
    fetch_indexes,
    fetch_jsonb_column_type,
    fetch_timestamp_columns,
    fetch_unique_constraints,
    grant_service_role_privileges,
    list_public_tables,
    psql,
    psql_expect_failure,
    run_audit_transaction_probe,
    table_count,
)

pytestmark = pytest.mark.integration


def test_audit_starts_from_empty_database(postgres_proof_stack: int) -> None:
    tables = list_public_tables(AUDIT_DATABASE)
    assert tables == set()
    version = psql(
        "SELECT to_regclass('public.alembic_version');",
        database=AUDIT_DATABASE,
    )
    assert version == ""


def test_audit_alembic_upgrade_head_and_single_head(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    assert_single_head(AUDIT_SERVICE, owner_url, AUDIT_EXPECTED_HEAD)
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)
    assert list_public_tables(AUDIT_DATABASE) == AUDIT_TABLES
    assert alembic_current_revision(AUDIT_SERVICE, owner_url) == AUDIT_EXPECTED_HEAD


def test_audit_schema_constraints_and_types(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)

    assert (
        fetch_jsonb_column_type(AUDIT_DATABASE, "legacy_audit_observations", "safe_metadata")
        == "jsonb"
    )

    inbox_timestamps = fetch_timestamp_columns(AUDIT_DATABASE, "audit_integration_inbox")
    assert {"first_received_at", "last_received_at"}.issubset(inbox_timestamps)
    observation_timestamps = fetch_timestamp_columns(AUDIT_DATABASE, "legacy_audit_observations")
    assert {"occurred_at", "received_at"}.issubset(observation_timestamps)

    uniques = fetch_unique_constraints(AUDIT_DATABASE)
    assert "uq_audit_inbox_consumer_event" in uniques
    assert "uq_legacy_audit_observation_source_row" in uniques

    indexes = fetch_indexes(AUDIT_DATABASE)
    assert "ix_audit_inbox_consumer_status" in indexes
    assert "ix_legacy_audit_observation_audit_entry_id" in indexes
    assert "ix_legacy_audit_observation_entity" in indexes
    assert "ix_legacy_audit_observation_occurred_at" in indexes


def test_audit_service_role_isolated_from_bridge_database(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)
    grant_service_role_privileges(database=AUDIT_DATABASE, role=AUDIT_ROLE)

    psql_expect_failure(
        "SELECT 1;",
        user=AUDIT_ROLE,
        database=BRIDGE_DATABASE,
    )


def test_audit_upgrade_head_is_idempotent(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)
    before_tables = {table: table_count(AUDIT_DATABASE, table) for table in AUDIT_TABLES}
    before_revision = alembic_current_revision(AUDIT_SERVICE, owner_url)

    alembic_upgrade_head(AUDIT_SERVICE, owner_url)

    after_tables = {table: table_count(AUDIT_DATABASE, table) for table in AUDIT_TABLES}
    after_revision = alembic_current_revision(AUDIT_SERVICE, owner_url)
    assert before_tables == after_tables
    assert before_revision == after_revision == AUDIT_EXPECTED_HEAD


def test_audit_schema_persists_after_postgres_restart(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)
    compose_restart_postgres()
    assert list_public_tables(AUDIT_DATABASE) == AUDIT_TABLES
    assert alembic_current_revision(AUDIT_SERVICE, owner_url) == AUDIT_EXPECTED_HEAD


def test_audit_repository_transactions(postgres_proof_stack: int) -> None:
    owner_url = audit_owner_url()
    alembic_upgrade_head(AUDIT_SERVICE, owner_url)
    grant_service_role_privileges(database=AUDIT_DATABASE, role=AUDIT_ROLE)

    result = run_audit_transaction_probe(audit_service_url())
    assert result["inbox_committed"] is True
    assert result["duplicate_blocked"] is True
    assert result["rollback_without_projection"] is True
    assert result["observation_count"] == 1
