"""PostgreSQL integration proof for Shipment migrations and acceptance persistence."""

from __future__ import annotations

from uuid import uuid4

import pytest

from .constants import (
    PICKUP_DATABASE,
    SHIPMENT_DATABASE,
    SHIPMENT_EXPECTED_HEAD,
    SHIPMENT_PRE_CUSTODY_REVISION,
    SHIPMENT_ROLE,
    SHIPMENT_TABLES,
)
from .helpers import (
    SHIPMENT_SERVICE,
    alembic_current_revision,
    alembic_upgrade,
    alembic_upgrade_head,
    assert_single_head,
    compose_restart_postgres,
    fetch_foreign_key_count,
    fetch_indexes,
    fetch_jsonb_column_type,
    fetch_timestamp_columns,
    fetch_unique_constraints,
    grant_service_role_privileges,
    list_public_tables,
    psql,
    psql_expect_failure,
    run_shipment_accepted_inbox_probe,
    run_shipment_http_probe,
    run_shipment_transaction_probe,
    shipment_alembic_url,
    shipment_service_url,
    table_count,
)

pytestmark = pytest.mark.integration


def test_shipment_starts_from_empty_database(postgres_proof_stack: int) -> None:
    _ = postgres_proof_stack
    tables = list_public_tables(SHIPMENT_DATABASE)
    assert tables == set()
    version = psql(
        "SELECT to_regclass('public.alembic_version');",
        database=SHIPMENT_DATABASE,
    )
    assert version == ""


def test_shipment_alembic_upgrade_head_and_single_head(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    assert_single_head(SHIPMENT_SERVICE, owner_url, SHIPMENT_EXPECTED_HEAD)
    alembic_upgrade(SHIPMENT_SERVICE, owner_url, SHIPMENT_PRE_CUSTODY_REVISION)
    assert alembic_current_revision(SHIPMENT_SERVICE, owner_url) == SHIPMENT_PRE_CUSTODY_REVISION

    driver_shipment_id = uuid4()
    psql(
        "INSERT INTO shipments ("
        "shipment_id, order_id, waybill_number, current_status, order_created_at, "
        "accepted_at, sla_started_at, current_custody_type, current_custody_id, version"
        ") VALUES ("
        f"'{driver_shipment_id}', '{uuid4()}', 'WB-DRIVER-MIG-001', 'IN_CUSTODY', "
        "TIMESTAMPTZ '2026-09-04 10:00:00+00', TIMESTAMPTZ '2026-09-04 11:00:00+00', "
        "TIMESTAMPTZ '2026-09-04 11:00:00+00', 'DRIVER', 'driver-legacy', 1"
        ");",
        database=SHIPMENT_DATABASE,
        tuples_only=False,
    )
    before = psql(
        f"SELECT current_custody_type FROM shipments WHERE shipment_id = '{driver_shipment_id}';",
        database=SHIPMENT_DATABASE,
    )
    assert before == "DRIVER"

    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    assert list_public_tables(SHIPMENT_DATABASE) == SHIPMENT_TABLES
    assert alembic_current_revision(SHIPMENT_SERVICE, owner_url) == SHIPMENT_EXPECTED_HEAD
    after = psql(
        f"SELECT current_custody_type FROM shipments WHERE shipment_id = '{driver_shipment_id}';",
        database=SHIPMENT_DATABASE,
    )
    assert after == "PICKUP_DRIVER"


def test_shipment_schema_constraints_and_types(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)

    assert fetch_jsonb_column_type(SHIPMENT_DATABASE, "acceptance_audit_logs", "details") == "jsonb"
    assert (
        fetch_jsonb_column_type(SHIPMENT_DATABASE, "acceptance_decisions", "exception_evidence")
        == "jsonb"
    )

    shipment_timestamps = fetch_timestamp_columns(SHIPMENT_DATABASE, "shipments")
    assert {"order_created_at", "accepted_at", "sla_started_at"}.issubset(shipment_timestamps)

    uniques = fetch_unique_constraints(SHIPMENT_DATABASE)
    assert "uq_acceptance_decisions_shipment_id" in uniques
    assert "uq_acceptance_decisions_pickup_task_id" in uniques
    assert "uq_shipment_events_shipment_event_type" in uniques
    assert "uq_shipments_waybill_number" in uniques
    assert "uq_shipment_inbox_consumer_event" in uniques

    indexes = fetch_indexes(SHIPMENT_DATABASE)
    assert "ix_shipments_order_id" in indexes
    assert "ix_pickup_task_snapshots_shipment_id" in indexes
    assert "ix_shipment_events_shipment_id" in indexes
    assert "ix_acceptance_audit_logs_entity" in indexes
    assert "ix_shipment_inbox_consumer_status" in indexes

    assert fetch_foreign_key_count(SHIPMENT_DATABASE) == 0


def test_shipment_service_role_isolated_from_pickup_database(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    grant_service_role_privileges(database=SHIPMENT_DATABASE, role=SHIPMENT_ROLE)

    psql_expect_failure(
        "SELECT 1;",
        user=SHIPMENT_ROLE,
        database=PICKUP_DATABASE,
    )


def test_shipment_upgrade_head_is_idempotent(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    before_tables = {table: table_count(SHIPMENT_DATABASE, table) for table in SHIPMENT_TABLES}
    before_revision = alembic_current_revision(SHIPMENT_SERVICE, owner_url)

    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)

    after_tables = {table: table_count(SHIPMENT_DATABASE, table) for table in SHIPMENT_TABLES}
    after_revision = alembic_current_revision(SHIPMENT_SERVICE, owner_url)
    assert before_tables == after_tables
    assert before_revision == after_revision == SHIPMENT_EXPECTED_HEAD


def test_shipment_schema_persists_after_postgres_restart(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    compose_restart_postgres()
    assert list_public_tables(SHIPMENT_DATABASE) == SHIPMENT_TABLES
    assert alembic_current_revision(SHIPMENT_SERVICE, owner_url) == SHIPMENT_EXPECTED_HEAD


def test_shipment_acceptance_transactions(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    grant_service_role_privileges(database=SHIPMENT_DATABASE, role=SHIPMENT_ROLE)

    result = run_shipment_transaction_probe(shipment_service_url())
    assert result["acceptance_committed"] is True
    assert result["evidence_uri_only"] is True
    assert result["duplicate_rejected"] is True
    assert result["rollback_without_partial"] is True
    assert result["stale_write_rejected"] is True


def test_shipment_http_acceptance_against_postgres(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    assert alembic_current_revision(SHIPMENT_SERVICE, owner_url) == SHIPMENT_EXPECTED_HEAD
    grant_service_role_privileges(database=SHIPMENT_DATABASE, role=SHIPMENT_ROLE)

    result = run_shipment_http_probe(shipment_service_url())
    assert result["acceptance_committed"] is True
    assert result["rows_persisted"] is True
    assert result["idempotent_replay"] is True
    assert result["conflicting_key"] is True
    assert result["no_partial_on_invalid"] is True
    assert result["unauthenticated_401"] is True
    assert result["identity_headers_ignored"] is True
    assert result["health_liveness"] is True
    assert result["ready_reports_blockers"] is True


def test_shipment_accepted_inbox_transactions(postgres_proof_stack: int) -> None:
    owner_url = shipment_alembic_url()
    alembic_upgrade_head(SHIPMENT_SERVICE, owner_url)
    assert alembic_current_revision(SHIPMENT_SERVICE, owner_url) == SHIPMENT_EXPECTED_HEAD
    grant_service_role_privileges(database=SHIPMENT_DATABASE, role=SHIPMENT_ROLE)

    result = run_shipment_accepted_inbox_probe(shipment_service_url())
    assert result["atomic_apply"] is True
    assert result["independent_version"] is True
    assert result["processed_duplicate"] is True
    assert result["http_conflict_quarantine"] is True
    assert result["invalid_contract_quarantine"] is True
    assert result["rollback_without_partial"] is True
    assert result["isolation_enforced"] is True
