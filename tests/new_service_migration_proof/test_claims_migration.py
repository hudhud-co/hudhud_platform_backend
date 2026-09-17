"""Prove the Claims migration against a real, disposable PostgreSQL 16.

The rules this schema carries are the ones a concurrent request could otherwise break,
so the tests try to break each of them for real, with no Python in the way:

* approve a claim whose custody review was never recorded (CLM-03);
* store an amount on a claim nobody approved;
* open a second claim on a parcel that already has one open (CLM-02);
* file a claim about a parcel the receiver took inside to test (v6.3 p.37);
* resolve a driver incident without saying what was found (OPS-07);
* put a compensation figure on a driver incident (SEC-07) — there is no column to
  put it in, and this proves that in PostgreSQL rather than in the models.

A constraint that was written but never exercised is not a guard.
"""

from __future__ import annotations

import pytest

from .helpers import (
    alembic,
    docker_available,
    model_tables,
    reflect,
    run_in_service,
    start_postgres,
    stop_postgres,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not docker_available(), reason="Docker is required for the migration proof"
    ),
]

SERVICE = "claims"
MODELS = "claims.infrastructure.persistence.models"
EXPECTED_HEAD = "w28_claims_core_001"

TRACKING = "SHP-20260915-000001"


@pytest.fixture(scope="module")
def applied():
    lab = start_postgres(SERVICE)
    try:
        upgrade = alembic(SERVICE, lab, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-4000:]
        yield lab, reflect(SERVICE, lab)
    finally:
        stop_postgres(lab)


def sql(lab, statements: str) -> str:
    """Run raw SQL in the service's venv and hand back what it printed."""
    script = f"""
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["CLAIMS_DATABASE_URL"])
{statements}
"""
    result = run_in_service(SERVICE, script, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def _insert_claim(
    *,
    claim_id: str,
    reference: str,
    tracking_code: str = TRACKING,
    status: str = "SUBMITTED",
    reviewed: str = "false",
    extra_columns: str = "",
    extra_values: str = "",
    boundary: str = "IN_HUDHUD_CUSTODY",
) -> str:
    return f"""
        c.execute(text(
            "INSERT INTO claims_compensation_claims "
            "(claim_id, reference, tracking_code, kind, opened_by, custody_boundary, "
            " status, submitted_at, custody_records_reviewed, version{extra_columns}) "
            "VALUES (:id, :ref, :tc, 'LOST_PARCEL', 'SENDER', '{boundary}', "
            " '{status}', now(), {reviewed}, 1{extra_values})"
        ), {{"id": {claim_id!r}, "ref": {reference!r}, "tc": {tracking_code!r}}})
"""


# ------------------------------------------------- the migration applies


def test_the_migration_applies_cleanly(applied) -> None:
    _, schema = applied
    assert "alembic_version" in schema


def test_the_head_is_the_revision_the_service_declares(applied) -> None:
    lab, _ = applied
    assert EXPECTED_HEAD in alembic(SERVICE, lab, "current").stdout


def test_every_model_table_exists_in_postgres(applied) -> None:
    _, schema = applied
    assert sorted(set(model_tables(SERVICE, MODELS)) - set(schema)) == []


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
    assert sorted(set(schema) - expected - {"alembic_version"}) == []


def test_no_monetary_column_is_a_numeric_or_a_float(applied) -> None:
    """Exact IQD (ADR-0012): a compensation figure is an integer count of dinars."""
    _, schema = applied
    offenders = [
        f"{table}.{name}: {column['type']}"
        for table, detail in schema.items()
        for name, column in detail["columns"].items()
        if name.endswith("_minor_units") and "INT" not in column["type"].upper()
    ]
    assert offenders == []


def test_postgres_holds_no_float_column_at_all(applied) -> None:
    _, schema = applied
    offenders = [
        f"{table}.{name}"
        for table, detail in schema.items()
        for name, column in detail["columns"].items()
        if any(
            kind in column["type"].upper()
            for kind in ("DOUBLE", "REAL", "NUMERIC", "FLOAT")
        )
    ]
    assert offenders == []


# ------------------------------------------------- SEC-07, in the database


def test_the_incident_table_has_no_column_for_a_compensation_value(applied) -> None:
    """SEC-07 — "No compensation or claim value is shown to the driver".

    The models say so and the migration says so; this says PostgreSQL agrees. A
    promise kept by remembering to filter is one query away from being broken.
    """
    _, schema = applied
    columns = schema["claims_driver_incidents"]["columns"]
    assert not [
        name
        for name in columns
        if "compensation" in name or "amount" in name or "minor_units" in name
    ]


def test_writing_an_amount_onto_an_incident_is_impossible(applied) -> None:
    """Not refused — impossible. There is no column to name."""
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import ProgrammingError
try:
    with engine.begin() as c:
        c.execute(text(
            "UPDATE claims_driver_incidents SET compensation_minor_units = 1"
        ))
    print("ACCEPTED")
except ProgrammingError as exc:
    print("REFUSED:" + type(exc.orig).__name__)
""",
    )
    assert "REFUSED:UndefinedColumn" in output


# ------------------------------------------------- CLM-03 and CLM-04


def test_a_claim_cannot_be_approved_without_a_recorded_custody_review(applied) -> None:
    """CLM-03 — "Hudhud reviews scan and custody records before compensating"."""
    lab, _ = applied
    body = _insert_claim(
        claim_id="a1111111-1111-4111-8111-111111111111",
        reference="CLM-20260915-000101",
        tracking_code="SHP-20260915-000101",
        status="APPROVED",
        reviewed="false",
        extra_columns=", compensation_minor_units, compensation_currency, "
        "decided_at, decided_by_actor_id",
        extra_values=", 450000, 'IQD', now(), "
        "'b1111111-1111-4111-8111-111111111111'",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
{body}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_claim_decided_after_custody_review" in output


def test_an_approved_claim_must_carry_an_amount_and_a_decider(applied) -> None:
    """CLM-04 — approved ⇒ the sender is compensated, by someone with a name."""
    lab, _ = applied
    body = _insert_claim(
        claim_id="a2222222-2222-4222-8222-222222222222",
        reference="CLM-20260915-000102",
        tracking_code="SHP-20260915-000102",
        status="APPROVED",
        reviewed="true",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
{body}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_claim_approved_carries_an_amount" in output


def test_an_amount_cannot_be_stored_on_a_claim_nobody_approved(applied) -> None:
    """A figure on an undecided claim looks like a decision nobody made."""
    lab, _ = applied
    body = _insert_claim(
        claim_id="a3333333-3333-4333-8333-333333333333",
        reference="CLM-20260915-000103",
        tracking_code="SHP-20260915-000103",
        status="UNDER_REVIEW",
        reviewed="true",
        extra_columns=", compensation_minor_units, compensation_currency",
        extra_values=", 450000, 'IQD'",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
{body}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_claim_amount_only_when_approved" in output


def test_a_rejection_must_carry_a_reason(applied) -> None:
    """v6.3 p.43 — "rejected ⇒ documented reason given"."""
    lab, _ = applied
    body = _insert_claim(
        claim_id="a4444444-4444-4444-8444-444444444444",
        reference="CLM-20260915-000104",
        tracking_code="SHP-20260915-000104",
        status="REJECTED",
        reviewed="true",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
{body}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_claim_rejected_carries_a_reason" in output


# ------------------------------------------------- CLM-02 and p.37


def test_a_second_open_claim_on_one_parcel_is_refused_by_the_index(applied) -> None:
    """CLM-02 — two agents filing in the same instant is what Python checks miss."""
    lab, _ = applied
    first = _insert_claim(
        claim_id="a5555555-5555-4555-8555-555555555555",
        reference="CLM-20260915-000105",
        tracking_code="SHP-20260915-000105",
    )
    second = _insert_claim(
        claim_id="a6666666-6666-4666-8666-666666666666",
        reference="CLM-20260915-000106",
        tracking_code="SHP-20260915-000105",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
with engine.begin() as c:
{first}
try:
    with engine.begin() as c:
{second}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "uq_claims_one_open_per_parcel" in output


def test_a_closed_claim_frees_the_parcel_for_another(applied) -> None:
    """The index is partial: a withdrawn claim does not block a later, better one."""
    lab, _ = applied
    first = _insert_claim(
        claim_id="a7777777-7777-4777-8777-777777777777",
        reference="CLM-20260915-000107",
        tracking_code="SHP-20260915-000107",
        status="WITHDRAWN",
    )
    second = _insert_claim(
        claim_id="a8888888-8888-4888-8888-888888888888",
        reference="CLM-20260915-000108",
        tracking_code="SHP-20260915-000107",
    )
    output = sql(
        lab,
        f"""
with engine.begin() as c:
{first}
{second}
print("ACCEPTED")
""",
    )
    assert "ACCEPTED" in output


def test_a_parcel_taken_inside_to_test_cannot_have_a_claim_row(applied) -> None:
    """v6.3 p.37 — HUDHUD's liability ended there, so there is no claim to store."""
    lab, _ = applied
    body = _insert_claim(
        claim_id="a9999999-9999-4999-8999-999999999999",
        reference="CLM-20260915-000109",
        tracking_code="SHP-20260915-000109",
        boundary="TAKEN_INSIDE_TO_TEST",
    )
    output = sql(
        lab,
        f"""
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
{body}
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_claim_filed_within_hudhud_liability" in output


# ------------------------------------------------- DRV-P25 and OPS-07


def test_a_resolved_incident_must_say_what_was_found(applied) -> None:
    """OPS-07 — operations closes it, and a blank note is not a resolution."""
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO claims_driver_incidents "
            "(incident_id, reference, kind, reported_by_driver_id, reported_at, "
            " status, resolved_at, resolved_by_actor_id, resolution_note, version) "
            "VALUES ('c1111111-1111-4111-8111-111111111111', 'CLM-20260915-000201', "
            " 'VEHICLE_OR_SAFETY_ISSUE', "
            " 'd1111111-1111-4111-8111-111111111111', now(), 'RESOLVED', now(), "
            " 'e1111111-1111-4111-8111-111111111111', '   ', 1)"
        ))
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_incident_resolved_is_documented" in output


def test_a_parcel_incident_without_a_parcel_is_refused(applied) -> None:
    """Driver App v8 `needsParcel` — everything but a breakdown is about one parcel."""
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO claims_driver_incidents "
            "(incident_id, reference, kind, reported_by_driver_id, reported_at, "
            " status, version) "
            "VALUES ('c2222222-2222-4222-8222-222222222222', 'CLM-20260915-000202', "
            " 'PARCEL_MISSING', 'd1111111-1111-4111-8111-111111111111', now(), "
            " 'SUBMITTED', 1)"
        ))
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_incident_parcel_matches_kind" in output


def test_a_breakdown_with_a_parcel_is_refused_too(applied) -> None:
    """The constraint is an equivalence, not a one-way check."""
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO claims_driver_incidents "
            "(incident_id, reference, kind, reported_by_driver_id, reported_at, "
            " tracking_code, status, version) "
            "VALUES ('c3333333-3333-4333-8333-333333333333', 'CLM-20260915-000203', "
            " 'VEHICLE_OR_SAFETY_ISSUE', 'd1111111-1111-4111-8111-111111111111', "
            " now(), 'SHP-20260915-000203', 'SUBMITTED', 1)"
        ))
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "ck_claims_incident_parcel_matches_kind" in output


# ------------------------------------------------- the reference series


def test_two_simultaneous_filers_never_get_the_same_reference(applied) -> None:
    """The counter is allocated in one statement, not read and incremented."""
    lab, _ = applied
    output = sql(
        lab,
        """
from concurrent.futures import ThreadPoolExecutor

def allocate(_):
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO claims_reference_sequences (scope, day, last_sequence) "
            "VALUES ('reference', '20260915', 1) "
            "ON CONFLICT (scope, day) DO UPDATE "
            "SET last_sequence = claims_reference_sequences.last_sequence + 1 "
            "RETURNING last_sequence"
        )).scalar_one()

with ThreadPoolExecutor(max_workers=8) as pool:
    allocated = list(pool.map(allocate, range(24)))
print("DISTINCT:" + str(len(set(allocated))) + "/" + str(len(allocated)))
""",
    )
    assert "DISTINCT:24/24" in output


# ------------------------------------------------- the outbox and inbox


def test_one_event_is_never_published_twice(applied) -> None:
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
row = (
    "INSERT INTO claims_integration_outbox "
    "(id, event_id, subject, event_type, event_version, aggregate_id, "
    " aggregate_version, payload_json, status, next_attempt_at, created_at) "
    "VALUES (:id, 'f1111111-1111-4111-8111-111111111111', 'claims.v1.test', "
    " 'ClaimApproved', 1, 'f2222222-2222-4222-8222-222222222222', :v, "
    " '{}'::jsonb, 'pending', now(), now())"
)
with engine.begin() as c:
    c.execute(text(row), {"id": "f3333333-3333-4333-8333-333333333333", "v": 1})
try:
    with engine.begin() as c:
        c.execute(text(row), {"id": "f4444444-4444-4444-8444-444444444444", "v": 2})
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "uq_claims_outbox_event_id" in output


def test_two_consumers_each_get_their_own_chance_at_one_event(applied) -> None:
    """Uniqueness is `(consumer, event)`, so a redelivery to one is still deduped."""
    lab, _ = applied
    output = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
row = (
    "INSERT INTO claims_integration_inbox "
    "(inbox_id, consumer_name, event_id, event_type, event_version, status, "
    " received_at, payload_json) "
    "VALUES (:id, :consumer, '11111111-2222-4333-8444-555555555555', "
    " 'ParcelDelivered', 1, 'received', now(), '{}'::jsonb)"
)
with engine.begin() as c:
    c.execute(text(row), {"id": "21111111-1111-4111-8111-111111111111",
                          "consumer": "claims.custody"})
    c.execute(text(row), {"id": "22222222-2222-4222-8222-222222222222",
                          "consumer": "claims.notifications"})
try:
    with engine.begin() as c:
        c.execute(text(row), {"id": "23333333-3333-4333-8333-333333333333",
                              "consumer": "claims.custody"})
    print("ACCEPTED")
except IntegrityError as exc:
    print("REFUSED:" + str(exc.orig))
""",
    )
    assert "ACCEPTED" not in output
    assert "uq_claims_inbox_consumer_event" in output
