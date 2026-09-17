"""Prove the Notification migration against a real, disposable PostgreSQL 16.

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

SERVICE = "notification"
MODELS = "notification.infrastructure.persistence.models"
EXPECTED_HEAD = "w24_notification_core_001"


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


def test_the_recipient_key_is_checked_to_be_a_digest_in_postgres(applied) -> None:
    """Only a keyed digest reaches this database, never a phone number."""
    _, schema = applied
    checks = schema["notification_recipients"]["check_constraints"]
    assert "ck_notification_recipient_key_is_digest" in checks
    assert "ck_notification_recipient_last4" in checks


def test_the_acceptance_sms_opt_out_is_impossible_in_postgres(applied) -> None:
    """NTF-03 — a direct write cannot create the opt-out the service refuses."""
    _, schema = applied
    assert (
        "ck_notification_acceptance_sms_always_on"
        in schema["notification_preferences"]["check_constraints"]
    )


def test_one_message_per_fact_recipient_and_channel(applied) -> None:
    _, schema = applied
    assert "uq_notification_dedupe_key" in schema["notifications"]["unique_constraints"]


def test_a_failed_notification_always_records_why(applied) -> None:
    _, schema = applied
    assert (
        "ck_notification_failure_has_reason"
        in schema["notifications"]["check_constraints"]
    )


def test_no_column_in_postgres_could_hold_a_phone_number(applied) -> None:
    _, schema = applied
    suspicious = [
        f"{table}.{column}"
        for table, definition in schema.items()
        for column in definition["columns"]
        if "phone" in column and column != "phone_last4"
    ]
    assert suspicious == []


def test_no_column_in_postgres_could_hold_a_delivery_code(applied) -> None:
    _, schema = applied
    allowed = {"tracking_code", "template_code", "dedupe_key", "last_error_code"}
    suspicious = [
        f"{table}.{column}"
        for table, definition in schema.items()
        for column in definition["columns"]
        if ("code" in column or "otp" in column) and column not in allowed
    ]
    assert suspicious == []


def test_postgres_built_no_outbox_table(applied) -> None:
    """Notification consumes journey facts and publishes none."""
    _, schema = applied
    assert not [name for name in schema if "outbox" in name]


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_notification_inbox_consumer_event"
        in schema["notification_integration_inbox"]["unique_constraints"]
    )


def test_the_unread_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["notification_centre_entries"]["indexes"][
        "ix_notification_centre_unread"
    ]
    assert index["columns"] == ["principal_id"]


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
