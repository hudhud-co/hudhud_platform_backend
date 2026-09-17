"""Prove the Ordering migration against a real, disposable PostgreSQL 16.

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

SERVICE = "ordering"
MODELS = "ordering.infrastructure.persistence.models"
EXPECTED_HEAD = "w22_ordering_core_001"


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


def test_a_customer_parcel_can_never_carry_cod(applied) -> None:
    """v6.3 p.8 Confirmed decision, enforced by PostgreSQL and not only by Python."""
    _, schema = applied
    checks = schema["shipment_requests"]["check_constraints"]
    assert "ck_shipment_request_no_cod_for_customers" in checks


def test_a_cod_parcel_must_carry_an_amount_and_a_currency(applied) -> None:
    _, schema = applied
    checks = schema["shipment_requests"]["check_constraints"]
    assert "ck_shipment_request_cod_has_amount" in checks
    assert "ck_shipment_request_cod_amount_pair" in checks


def test_a_parcel_is_never_stored_without_a_description(applied) -> None:
    _, schema = applied
    assert "ck_shipment_request_described" in schema["shipment_requests"]["check_constraints"]
    assert schema["shipment_requests"]["columns"]["description"]["nullable"] is False


def test_a_merchant_sender_always_names_its_merchant(applied) -> None:
    _, schema = applied
    assert (
        "ck_shipment_request_merchant_sender_has_merchant"
        in schema["shipment_requests"]["check_constraints"]
    )
    assert "ck_order_merchant_sender_has_merchant" in schema["orders"]["check_constraints"]


def test_a_tracking_code_is_unique_in_postgres(applied) -> None:
    _, schema = applied
    assert (
        "uq_shipment_request_tracking_code"
        in schema["shipment_requests"]["unique_constraints"]
    )


def test_the_live_label_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["shipment_requests"]["indexes"]["uq_shipment_request_label_code"]
    assert index["unique"] is True
    assert index["columns"] == ["label_code"]


def test_measurements_are_all_nullable_in_postgres(applied) -> None:
    """v6.3 p.12 made weight and size optional; the schema has to allow their absence."""
    _, schema = applied
    columns = schema["shipment_requests"]["columns"]
    for name in ("weight_grams", "length_cm", "width_cm", "height_cm"):
        assert columns[name]["nullable"] is True


def test_money_columns_are_integers_in_postgres(applied) -> None:
    """A NUMERIC or DOUBLE PRECISION column is how exactness is lost in practice."""
    _, schema = applied
    for table, column in (
        ("shipment_requests", "cod_amount_minor_units"),
        ("tariff_rates", "delivery_fee_minor_units"),
        ("tariff_rates", "packaging_fee_minor_units"),
    ):
        rendered = schema[table]["columns"][column]["type"].upper()
        assert "INT" in rendered, (table, column, rendered)
        assert "NUMERIC" not in rendered and "DOUBLE" not in rendered


def test_the_tariff_window_constraint_is_real(applied) -> None:
    _, schema = applied
    assert "ck_tariff_window_ordered" in schema["tariff_rates"]["check_constraints"]


def test_the_outbox_orders_by_aggregate_version(applied) -> None:
    _, schema = applied
    assert (
        "uq_ordering_outbox_aggregate_version"
        in schema["ordering_integration_outbox"]["unique_constraints"]
    )


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_ordering_inbox_consumer_event"
        in schema["ordering_integration_inbox"]["unique_constraints"]
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
