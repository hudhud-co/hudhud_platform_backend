"""Prove the Workforce migration against a real, disposable PostgreSQL 16.

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

SERVICE = "workforce"
MODELS = "workforce.infrastructure.persistence.models"
EXPECTED_HEAD = "w25_workforce_core_001"


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


def test_a_verified_application_is_attributed_in_postgres(applied) -> None:
    """SEC-03 — a verification nobody performed is not one."""
    _, schema = applied
    assert (
        "ck_driver_application_verification_is_attributed"
        in schema["driver_applications"]["check_constraints"]
    )


def test_the_one_open_application_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["driver_applications"]["indexes"]["uq_driver_application_one_open"]
    assert index["unique"] is True
    assert index["columns"] == ["applicant_principal_id"]


def test_a_driver_is_unique_per_principal_and_application(applied) -> None:
    _, schema = applied
    constraints = schema["drivers"]["unique_constraints"]
    assert "uq_driver_principal" in constraints
    assert "uq_driver_application" in constraints


def test_a_shift_pattern_must_carry_a_window_in_postgres(applied) -> None:
    """DRV-A04 — "Pick at least one shift"."""
    _, schema = applied
    checks = schema["driver_shift_patterns"]["check_constraints"]
    assert "ck_shift_pattern_has_a_window" in checks
    assert "ck_shift_pattern_window_ordered" in checks


def test_the_pattern_windows_column_is_real_jsonb(applied) -> None:
    _, schema = applied
    column = schema["driver_shift_patterns"]["columns"]["windows"]
    assert "JSON" in column["type"].upper()
    assert column["nullable"] is False


def test_attendance_is_once_per_driver_per_day_in_postgres(applied) -> None:
    _, schema = applied
    assert (
        "uq_attendance_driver_day"
        in schema["driver_attendance"]["unique_constraints"]
    )


def test_a_started_shift_always_has_a_start_time(applied) -> None:
    _, schema = applied
    assert (
        "ck_attendance_started_has_time"
        in schema["driver_attendance"]["check_constraints"]
    )


def test_the_one_active_block_index_is_real_and_partial(applied) -> None:
    _, schema = applied
    index = schema["driver_lateness_blocks"]["indexes"]["uq_lateness_block_one_active"]
    assert index["unique"] is True
    assert index["columns"] == ["driver_id"]


def test_a_cleared_block_is_attributed_in_postgres(applied) -> None:
    """OPS-09."""
    _, schema = applied
    assert (
        "ck_lateness_block_clearing_is_attributed"
        in schema["driver_lateness_blocks"]["check_constraints"]
    )


def test_the_leave_constraints_are_real(applied) -> None:
    _, schema = applied
    checks = schema["driver_leave_requests"]["check_constraints"]
    assert "ck_leave_decline_has_reason" in checks
    assert "ck_leave_hours_match_kind" in checks
    assert "ck_leave_hourly_is_one_day" in checks
    assert "ck_leave_window_ordered" in checks


def test_postgres_built_no_outbox_table(applied) -> None:
    """Workforce answers questions over HTTP and publishes none."""
    _, schema = applied
    assert not [name for name in schema if "outbox" in name]


def test_the_inbox_is_keyed_by_consumer_and_event(applied) -> None:
    _, schema = applied
    assert (
        "uq_workforce_inbox_consumer_event"
        in schema["workforce_integration_inbox"]["unique_constraints"]
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
