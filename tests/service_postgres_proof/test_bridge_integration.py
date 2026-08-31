"""PostgreSQL integration proof for legacy_event_bridge migrations and persistence."""

from __future__ import annotations

import pytest

from .constants import (
    BRIDGE_DATABASE,
    BRIDGE_EXPECTED_HEAD,
    BRIDGE_ROLE,
    BRIDGE_TABLES,
)
from .helpers import (
    AUDIT_DATABASE,
    BRIDGE_SERVICE,
    alembic_current_revision,
    alembic_upgrade_head,
    assert_single_head,
    bridge_owner_url,
    bridge_service_url,
    compose_restart_postgres,
    fetch_indexes,
    fetch_jsonb_column_type,
    fetch_partial_index_predicates,
    fetch_timestamp_columns,
    fetch_unique_constraints,
    grant_service_role_privileges,
    list_public_tables,
    psql,
    psql_expect_failure,
    run_bridge_transaction_probe,
    table_count,
)

pytestmark = pytest.mark.integration


def test_bridge_starts_from_empty_database(postgres_proof_stack: int) -> None:
    tables = list_public_tables(BRIDGE_DATABASE)
    assert tables == set()
    version = psql(
        "SELECT to_regclass('public.alembic_version');",
        database=BRIDGE_DATABASE,
    )
    assert version == ""


def test_bridge_alembic_upgrade_head_and_single_head(postgres_proof_stack: int) -> None:
    _ = postgres_proof_stack
    owner_url = bridge_owner_url()
    assert_single_head(BRIDGE_SERVICE, owner_url, BRIDGE_EXPECTED_HEAD)
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)
    assert list_public_tables(BRIDGE_DATABASE) == BRIDGE_TABLES
    assert alembic_current_revision(BRIDGE_SERVICE, owner_url) == BRIDGE_EXPECTED_HEAD


def test_bridge_schema_constraints_and_types(postgres_proof_stack: int) -> None:
    owner_url = bridge_owner_url()
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)

    assert (
        fetch_jsonb_column_type(BRIDGE_DATABASE, "bridge_landing", "normalized_fields") == "jsonb"
    )
    assert (
        fetch_jsonb_column_type(BRIDGE_DATABASE, "bridge_integration_outbox", "payload_json")
        == "jsonb"
    )

    timestamps = fetch_timestamp_columns(BRIDGE_DATABASE, "bridge_landing")
    assert "received_at" in timestamps

    uniques = fetch_unique_constraints(BRIDGE_DATABASE)
    assert "uq_bridge_landing_source_row" in uniques
    assert "uq_bridge_outbox_event_id" in uniques

    indexes = fetch_indexes(BRIDGE_DATABASE)
    assert "ix_bridge_landing_mapping_state" in indexes
    assert "ix_bridge_outbox_pending" in indexes

    partial = fetch_partial_index_predicates(BRIDGE_DATABASE)
    assert "ix_bridge_outbox_pending" in partial
    assert "pending" in partial["ix_bridge_outbox_pending"]
    assert "processing" in partial["ix_bridge_outbox_pending"]


def test_bridge_service_role_isolated_from_audit_database(postgres_proof_stack: int) -> None:
    owner_url = bridge_owner_url()
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)
    grant_service_role_privileges(database=BRIDGE_DATABASE, role=BRIDGE_ROLE)

    psql_expect_failure(
        "SELECT 1;",
        user=BRIDGE_ROLE,
        database=AUDIT_DATABASE,
    )


def test_bridge_upgrade_head_is_idempotent(postgres_proof_stack: int) -> None:
    owner_url = bridge_owner_url()
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)
    before_tables = {table: table_count(BRIDGE_DATABASE, table) for table in BRIDGE_TABLES}
    before_revision = alembic_current_revision(BRIDGE_SERVICE, owner_url)

    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)

    after_tables = {table: table_count(BRIDGE_DATABASE, table) for table in BRIDGE_TABLES}
    after_revision = alembic_current_revision(BRIDGE_SERVICE, owner_url)
    assert before_tables == after_tables
    assert before_revision == after_revision == BRIDGE_EXPECTED_HEAD


def test_bridge_schema_persists_after_postgres_restart(postgres_proof_stack: int) -> None:
    owner_url = bridge_owner_url()
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)
    compose_restart_postgres()
    assert list_public_tables(BRIDGE_DATABASE) == BRIDGE_TABLES
    assert alembic_current_revision(BRIDGE_SERVICE, owner_url) == BRIDGE_EXPECTED_HEAD


def test_bridge_repository_transactions(postgres_proof_stack: int) -> None:
    owner_url = bridge_owner_url()
    alembic_upgrade_head(BRIDGE_SERVICE, owner_url)
    grant_service_role_privileges(database=BRIDGE_DATABASE, role=BRIDGE_ROLE)

    result = run_bridge_transaction_probe(bridge_service_url())
    assert result["dedupe_inserted"] is True
    assert result["duplicate_blocked"] is True
    assert result["outbox_claimed"] == 1
    assert result["landing_checkpoint"] == "0/100"
