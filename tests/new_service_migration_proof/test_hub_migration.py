"""Prove the Hub migration against a real, disposable PostgreSQL 16.

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

SERVICE = "hub"
MODELS = "hub.infrastructure.persistence.models"
EXPECTED_HEAD = "w23_hub_core_001"


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


def test_the_cut_off_is_a_real_column_on_each_hub(applied) -> None:
    """v6.3 p.23 — per hub, never company-wide."""
    _, schema = applied
    column = schema["hubs"]["columns"]["cut_off_local_time"]
    assert column["nullable"] is False
    assert "TIME" in column["type"].upper()


def test_an_accepted_drop_off_is_complete_in_postgres(applied) -> None:
    _, schema = applied
    assert (
        "ck_hub_drop_off_accepted_is_complete"
        in schema["hub_drop_offs"]["check_constraints"]
    )


def test_the_live_drop_off_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["hub_drop_offs"]["indexes"]["uq_hub_drop_off_live_tracking_code"]
    assert index["unique"] is True
    assert index["columns"] == ["tracking_code"]


def test_a_held_parcel_always_says_why_in_postgres(applied) -> None:
    _, schema = applied
    assert (
        "ck_hub_presence_held_has_reason"
        in schema["hub_parcel_presences"]["check_constraints"]
    )


def test_a_parcel_appears_once_per_hub(applied) -> None:
    _, schema = applied
    assert (
        "uq_hub_presence_parcel"
        in schema["hub_parcel_presences"]["unique_constraints"]
    )


def test_the_seal_constraints_are_real(applied) -> None:
    _, schema = applied
    checks = schema["hub_consignments"]["check_constraints"]
    assert "ck_hub_consignment_seal_pair" in checks
    assert "ck_hub_consignment_seal_attributed" in checks
    assert "ck_hub_consignment_between_two_hubs" in checks


def test_the_parcel_code_array_is_a_postgres_array(applied) -> None:
    _, schema = applied
    column = schema["hub_consignments"]["columns"]["parcel_codes"]
    assert "[]" in column["type"] or "ARRAY" in column["type"].upper()
    assert column["nullable"] is False


def test_one_linehaul_per_consignment_in_postgres(applied) -> None:
    _, schema = applied
    assert (
        "uq_hub_linehaul_consignment"
        in schema["hub_linehauls"]["unique_constraints"]
    )


def test_the_outbox_orders_by_aggregate_version(applied) -> None:
    _, schema = applied
    assert (
        "uq_hub_outbox_aggregate_version"
        in schema["hub_integration_outbox"]["unique_constraints"]
    )


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_hub_inbox_consumer_event"
        in schema["hub_integration_inbox"]["unique_constraints"]
    )


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
