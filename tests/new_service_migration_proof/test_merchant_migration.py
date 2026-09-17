"""Prove the Merchant migration against a real, disposable PostgreSQL 16.

Static tests already assert that every model column appears somewhere in the migration
text. That catches typos, not semantics: a partial index with an invalid predicate, a
check constraint PostgreSQL rejects, or an array column the dialect will not create all
pass a text search and fail on the first real deployment. These tests apply the migration
for real and read the schema back.
"""

from __future__ import annotations

import pytest

from .helpers import (
    alembic,
    docker_available,
    model_tables,
    reflect,
    start_postgres,
    stop_postgres,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not docker_available(), reason="Docker is required for the migration proof"
    ),
]

SERVICE = "merchant"
MODELS = "merchant.infrastructure.persistence.models"
EXPECTED_HEAD = "w21_merchant_core_001"


@pytest.fixture(scope="module")
def applied():
    """One container per module: start, migrate to head, hand back the reflected schema."""
    lab = start_postgres(SERVICE)
    try:
        upgrade = alembic(SERVICE, lab, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-4000:]
        yield lab, reflect(SERVICE, lab)
    finally:
        stop_postgres(lab)


def test_the_migration_applies_cleanly(applied) -> None:
    _, schema = applied
    assert "alembic_version" in schema


def test_the_head_is_the_revision_the_service_declares(applied) -> None:
    lab, _ = applied
    current = alembic(SERVICE, lab, "current")
    assert EXPECTED_HEAD in current.stdout


def test_every_model_table_exists_in_postgres(applied) -> None:
    _, schema = applied
    expected = model_tables(SERVICE, MODELS)
    missing = sorted(set(expected) - set(schema))
    assert missing == []


def test_every_model_column_exists_in_postgres(applied) -> None:
    """The failure this catches is a column added to a model and forgotten in the DDL."""
    _, schema = applied
    expected = model_tables(SERVICE, MODELS)
    missing = {
        table: sorted(set(columns) - set(schema[table]["columns"]))
        for table, columns in expected.items()
        if set(columns) - set(schema.get(table, {}).get("columns", {}))
    }
    assert missing == {}


def test_postgres_created_no_table_the_models_do_not_know_about(applied) -> None:
    _, schema = applied
    expected = set(model_tables(SERVICE, MODELS))
    unexpected = sorted(set(schema) - expected - {"alembic_version"})
    assert unexpected == []


# --------------------------------------------------- invariants, really enforced


def test_the_one_open_application_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    indexes = schema["merchant_applications"]["indexes"]
    index = indexes["uq_merchant_application_one_open_per_applicant"]
    assert index["unique"] is True
    assert index["columns"] == ["applicant_principal_id"]


def test_the_one_default_pickup_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["merchant_stores"]["indexes"]["uq_merchant_store_one_default_pickup"]
    assert index["unique"] is True
    assert index["columns"] == ["merchant_id"]


def test_the_live_membership_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["merchant_team_memberships"]["indexes"][
        "uq_merchant_team_live_membership"
    ]
    assert index["unique"] is True
    assert index["columns"] == ["merchant_id", "invited_phone"]


def test_the_label_check_constraints_are_real(applied) -> None:
    _, schema = applied
    checks = schema["merchant_label_stock"]["check_constraints"]
    assert "ck_merchant_label_consumed_within_allocation" in checks
    assert "ck_merchant_label_self_print_authorized" in checks


def test_the_team_check_constraints_are_real(applied) -> None:
    _, schema = applied
    checks = schema["merchant_team_memberships"]["check_constraints"]
    assert "ck_merchant_team_active_has_principal" in checks
    assert "ck_merchant_team_has_branch" in checks


def test_the_outbox_orders_by_aggregate_version(applied) -> None:
    _, schema = applied
    assert (
        "uq_merchant_outbox_aggregate_version"
        in schema["merchant_integration_outbox"]["unique_constraints"]
    )


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_merchant_inbox_consumer_event"
        in schema["merchant_integration_inbox"]["unique_constraints"]
    )


def test_the_branch_array_column_is_a_postgres_array(applied) -> None:
    _, schema = applied
    column = schema["merchant_team_memberships"]["columns"]["store_ids"]
    assert "[]" in column["type"] or "ARRAY" in column["type"].upper()
    assert column["nullable"] is False


def test_the_migration_is_reversible() -> None:
    """A migration that cannot be undone cannot be rolled back in an incident."""
    lab = start_postgres(f"{SERVICE}-down")
    try:
        assert alembic(SERVICE, lab, "upgrade", "head").returncode == 0
        down = alembic(SERVICE, lab, "downgrade", "base")
        assert down.returncode == 0, down.stderr[-4000:]
        remaining = set(reflect(SERVICE, lab)) - {"alembic_version"}
        assert remaining == set()
    finally:
        stop_postgres(lab)


def test_the_seal_stock_constraint_is_real(applied) -> None:
    _, schema = applied
    checks = schema["merchant_label_stock"]["check_constraints"]
    assert "ck_merchant_seal_stock_is_hudhud_supplied" in checks
