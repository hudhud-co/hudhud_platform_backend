"""Prove the Delivery migration against a real, disposable PostgreSQL 16.

Beyond the usual "does it apply", two of these tests exist to keep a business decision
honest rather than to check DDL: the schema must contain **no** column that could hold a
delivery code and **no** column that could hold an identity-document photograph. Both
are searched for by name across every table, so adding one later trips this suite.
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

SERVICE = "delivery"
MODELS = "delivery.infrastructure.persistence.models"
EXPECTED_HEAD = "w26_delivery_core_001"


@pytest.fixture(scope="module")
def applied():
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


# --------------------------------------------- the two conflicts, in the schema


#: Columns whose name contains "code" but which hold something else entirely. Anything
#: text-typed outside this set is a place a delivery code could be written to.
_CODE_NAMED_BUT_NOT_A_CODE = frozenset(
    {
        "tracking_code",  # the parcel's public identifier
        "packaging_seal_code",  # the merchant's seal number (v6.3 p.14)
        "last_error_code",  # outbox and inbox failure classification
        "code_attempt_count",  # an integer counter, not a code
    }
)


def test_no_column_anywhere_could_hold_a_delivery_code(applied) -> None:
    """DRV-L05 — the code is never stored, only a keyed digest bound to the stop."""
    _, schema = applied
    offenders = [
        f"{table}.{column}"
        for table, detail in schema.items()
        for column in detail["columns"]
        if "code" in column
        and not column.endswith("_digest")
        and column not in _CODE_NAMED_BUT_NOT_A_CODE
    ]
    assert offenders == []


def test_the_code_named_columns_that_do_exist_are_what_they_claim(applied) -> None:
    """The allow-list above is only safe while each entry still means what it says."""
    _, schema = applied
    columns = schema["delivery_stops"]["columns"]
    assert "INT" in columns["code_attempt_count"]["type"].upper()
    assert columns["delivery_code_digest"]["type"].upper().startswith("VARCHAR(64)")


def test_no_column_anywhere_could_hold_an_identity_photograph(applied) -> None:
    """DRV-L07 — nothing is retained, so there is nothing to delete later."""
    _, schema = applied
    offenders = [
        f"{table}.{column}"
        for table, detail in schema.items()
        for column in detail["columns"]
        if ("id_photo" in column or "identity_document" in column or "id_image" in column)
    ]
    assert offenders == []


def test_the_code_digest_column_must_hold_a_digest(applied) -> None:
    _, schema = applied
    assert (
        "ck_delivery_stop_code_digest_is_a_digest"
        in schema["delivery_stops"]["check_constraints"]
    )


# --------------------------------------------------- invariants, really enforced


def test_a_delivered_stop_is_always_a_verified_stop(applied) -> None:
    """v6.3 p.26 — custody passes to the receiver only after verification."""
    _, schema = applied
    checks = schema["delivery_stops"]["check_constraints"]
    assert "ck_delivery_stop_delivered_is_verified" in checks
    assert "ck_delivery_stop_closed_at_matches_status" in checks
    assert "ck_delivery_stop_only_delivered_has_delivered_at" in checks


def test_one_live_stop_per_parcel_is_a_real_partial_index(applied) -> None:
    _, schema = applied
    index = schema["delivery_stops"]["indexes"]["uq_delivery_stop_one_live_per_parcel"]
    assert index["unique"] is True
    assert index["columns"] == ["tracking_code"]


def test_a_driver_carries_one_open_manifest(applied) -> None:
    _, schema = applied
    index = schema["delivery_manifests"]["indexes"][
        "uq_delivery_manifest_one_open_per_driver"
    ]
    assert index["unique"] is True
    assert index["columns"] == ["driver_principal_id"]


def test_a_pos_payment_carries_proof_in_postgres(applied) -> None:
    """Driver App v8 `lmPayApproved` — "PROOF OF PAYMENT — ONE IS REQUIRED"."""
    _, schema = applied
    checks = schema["delivery_payments"]["check_constraints"]
    assert "ck_delivery_payment_pos_has_proof" in checks
    assert "ck_delivery_payment_collected_has_amount" in checks
    assert (
        "uq_delivery_payment_one_per_stop"
        in schema["delivery_payments"]["unique_constraints"]
    )


def test_money_columns_are_integers_and_never_floats(applied) -> None:
    """Exact IQD representation: integer minor units, never a float or a NUMERIC."""
    _, schema = applied
    monetary = [
        (table, column, detail["columns"][column]["type"])
        for table, detail in schema.items()
        for column in detail["columns"]
        if column.endswith("_minor_units")
    ]
    assert monetary, "expected at least one monetary column"
    for table, column, type_name in monetary:
        assert "INT" in type_name.upper(), f"{table}.{column} is {type_name}"


def test_an_operations_decision_is_always_attributed(applied) -> None:
    """OPS-08 — "Held for next attempt — decided by operations"."""
    _, schema = applied
    checks = schema["delivery_failed_attempts"]["check_constraints"]
    assert "ck_delivery_failed_attempt_decision_has_time" in checks
    assert "ck_delivery_failed_attempt_decision_is_attributed" in checks


def test_a_parcel_is_rated_once(applied) -> None:
    """CUS-13 — the rating is offered once, after the handover."""
    _, schema = applied
    assert (
        "uq_delivery_rating_one_per_parcel"
        in schema["delivery_courier_ratings"]["unique_constraints"]
    )
    assert (
        "ck_delivery_rating_score_range"
        in schema["delivery_courier_ratings"]["check_constraints"]
    )


def test_a_driver_incident_names_the_stop_it_was_raised_from(applied) -> None:
    """DRV-L21 — an incident opened from nowhere is not one opened from a stop."""
    _, schema = applied
    assert (
        "ck_delivery_issue_driver_report_names_a_stop"
        in schema["delivery_issue_reports"]["check_constraints"]
    )


def test_a_handover_window_must_lie_within_a_day(applied) -> None:
    """CUS-11."""
    _, schema = applied
    checks = schema["delivery_receiver_preferences"]["check_constraints"]
    assert "ck_delivery_receiver_preference_window_is_complete" in checks
    assert "ck_delivery_receiver_preference_window_lies_within_a_day" in checks
    assert "ck_delivery_receiver_preference_geo_is_a_pair" in checks


def test_the_outbox_is_keyed_by_event_and_by_aggregate_version(applied) -> None:
    _, schema = applied
    constraints = schema["delivery_integration_outbox"]["unique_constraints"]
    assert "uq_delivery_outbox_event_id" in constraints
    assert "uq_delivery_outbox_aggregate_version" in constraints


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_delivery_inbox_consumer_event"
        in schema["delivery_integration_inbox"]["unique_constraints"]
    )


def test_the_migration_is_reversible() -> None:
    lab = start_postgres(f"{SERVICE}-down")
    try:
        assert alembic(SERVICE, lab, "upgrade", "head").returncode == 0
        down = alembic(SERVICE, lab, "downgrade", "base")
        assert down.returncode == 0, down.stderr[-4000:]
        remaining = set(reflect(SERVICE, lab)) - {"alembic_version"}
        assert remaining == set()
    finally:
        stop_postgres(lab)
