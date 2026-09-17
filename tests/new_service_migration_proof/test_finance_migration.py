"""Prove the Finance migration against a real, disposable PostgreSQL 16.

This one goes further than the other services' proofs, because the rules it is checking
are enforced by the database rather than by the service. A trigger that was written but
never fired is not a guard, so these tests try to break each one for real:

* update a posted ledger entry;
* delete one;
* commit an entry whose postings do not balance.

All three must be refused by PostgreSQL itself, with no Python in the way.
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

SERVICE = "finance"
MODELS = "finance.infrastructure.persistence.models"
EXPECTED_HEAD = "w27_finance_core_001"


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
engine = create_engine(os.environ["FINANCE_DATABASE_URL"])
{statements}
"""
    result = run_in_service(SERVICE, script, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


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


# ------------------------------------------------- the ledger is append-only


def _one_balanced_entry() -> str:
    """SQL that posts one valid entry, for the mutation tests to then attack."""
    return """
ENTRY = "11111111-1111-4111-8111-111111111111"
with engine.begin() as c:
    c.execute(text(
        "INSERT INTO finance_journal_entries "
        "(entry_id, reason, occurred_at, currency, total_minor_units, created_at) "
        "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 1000, now())"
    ), {"e": ENTRY})
    c.execute(text(
        "INSERT INTO finance_journal_postings "
        "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
        " currency, occurred_at, position) VALUES "
        "(gen_random_uuid(), :e, 'BANK', NULL, 'DEBIT', 1000, 'IQD', now(), 0), "
        "(gen_random_uuid(), :e, 'HUDHUD_REVENUE', NULL, 'CREDIT', 1000, 'IQD', now(), 1)"
    ), {"e": ENTRY})
"""


def test_a_posted_entry_cannot_be_updated(applied) -> None:
    """ADR-0012 — a mistake is corrected by posting its reverse, never by an edit."""
    lab, _ = applied
    out = sql(
        lab,
        _one_balanced_entry()
        + """
from sqlalchemy.exc import InternalError, DatabaseError
try:
    with engine.begin() as c:
        c.execute(text(
            "UPDATE finance_journal_entries SET total_minor_units = 999999 "
            "WHERE entry_id = :e"
        ), {"e": ENTRY})
except (InternalError, DatabaseError) as exc:
    assert "append-only" in str(exc), str(exc)
    print("UPDATE_REFUSED")
else:
    raise AssertionError("a posted ledger entry was updated")
""",
    )
    assert "UPDATE_REFUSED" in out


def test_a_posted_entry_cannot_be_deleted(applied) -> None:
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import InternalError, DatabaseError
ENTRY = "11111111-1111-4111-8111-111111111111"
try:
    with engine.begin() as c:
        c.execute(text("DELETE FROM finance_journal_entries WHERE entry_id = :e"),
                  {"e": ENTRY})
except (InternalError, DatabaseError) as exc:
    assert "append-only" in str(exc), str(exc)
    print("DELETE_REFUSED")
else:
    raise AssertionError("a posted ledger entry was deleted")
""",
    )
    assert "DELETE_REFUSED" in out


def test_a_posting_cannot_be_updated_or_deleted(applied) -> None:
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import InternalError, DatabaseError
refused = 0
for statement in (
    "UPDATE finance_journal_postings SET amount_minor_units = 1",
    "DELETE FROM finance_journal_postings",
):
    try:
        with engine.begin() as c:
            c.execute(text(statement))
    except (InternalError, DatabaseError) as exc:
        assert "append-only" in str(exc), str(exc)
        refused += 1
assert refused == 2, refused
print("POSTINGS_IMMUTABLE")
""",
    )
    assert "POSTINGS_IMMUTABLE" in out


# ------------------------------------------------- every entry balances, in the DB


def test_postgres_refuses_an_entry_that_does_not_balance(applied) -> None:
    """The rule the whole service rests on, checked where Python cannot be bypassed."""
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import InternalError, DatabaseError
E = "22222222-2222-4222-8222-222222222222"
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO finance_journal_entries "
            "(entry_id, reason, occurred_at, currency, total_minor_units, created_at) "
            "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 1000, now())"
        ), {"e": E})
        c.execute(text(
            "INSERT INTO finance_journal_postings "
            "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
            " currency, occurred_at, position) VALUES "
            "(gen_random_uuid(), :e, 'BANK', NULL, 'DEBIT', 1000, 'IQD', now(), 0), "
            "(gen_random_uuid(), :e, 'HUDHUD_REVENUE', NULL, 'CREDIT', 999, 'IQD', now(), 1)"
        ), {"e": E})
except (InternalError, DatabaseError) as exc:
    assert "does not balance" in str(exc), str(exc)
    print("UNBALANCED_REFUSED")
else:
    raise AssertionError("an unbalanced entry was committed")
""",
    )
    assert "UNBALANCED_REFUSED" in out


def test_postgres_refuses_a_single_sided_entry(applied) -> None:
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import InternalError, DatabaseError
E = "33333333-3333-4333-8333-333333333333"
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO finance_journal_entries "
            "(entry_id, reason, occurred_at, currency, total_minor_units, created_at) "
            "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 500, now())"
        ), {"e": E})
        c.execute(text(
            "INSERT INTO finance_journal_postings "
            "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
            " currency, occurred_at, position) VALUES "
            "(gen_random_uuid(), :e, 'BANK', NULL, 'DEBIT', 500, 'IQD', now(), 0)"
        ), {"e": E})
except (InternalError, DatabaseError) as exc:
    assert "posting" in str(exc), str(exc)
    print("SINGLE_SIDED_REFUSED")
else:
    raise AssertionError("a one-posting entry was committed")
""",
    )
    assert "SINGLE_SIDED_REFUSED" in out


def test_a_balanced_entry_still_commits(applied) -> None:
    """The guard must not be so strict that the normal case stops working."""
    lab, _ = applied
    out = sql(
        lab,
        """
E = "44444444-4444-4444-8444-444444444444"
D = "55555555-5555-4555-8555-555555555555"
with engine.begin() as c:
    c.execute(text(
        "INSERT INTO finance_journal_entries "
        "(entry_id, reason, occurred_at, currency, total_minor_units, created_at) "
        "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 105000, now())"
    ), {"e": E})
    c.execute(text(
        "INSERT INTO finance_journal_postings "
        "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
        " currency, occurred_at, position) VALUES "
        "(gen_random_uuid(), :e, 'DRIVER_CASH_CUSTODY', :d, 'DEBIT', 105000, 'IQD', now(), 0), "
        "(gen_random_uuid(), :e, 'MERCHANT_PAYABLE', :d, 'CREDIT', 100000, 'IQD', now(), 1), "
        "(gen_random_uuid(), :e, 'HUDHUD_REVENUE', NULL, 'CREDIT', 5000, 'IQD', now(), 2)"
    ), {"e": E, "d": D})
print("BALANCED_COMMITTED")
""",
    )
    assert "BALANCED_COMMITTED" in out


def test_a_party_scoped_account_must_name_its_party_in_postgres(applied) -> None:
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError
E = "66666666-6666-4666-8666-666666666666"
try:
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO finance_journal_entries "
            "(entry_id, reason, occurred_at, currency, total_minor_units, created_at) "
            "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 100, now())"
        ), {"e": E})
        c.execute(text(
            "INSERT INTO finance_journal_postings "
            "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
            " currency, occurred_at, position) VALUES "
            "(gen_random_uuid(), :e, 'DRIVER_CASH_CUSTODY', NULL, 'DEBIT', 100, 'IQD', now(), 0), "
            "(gen_random_uuid(), :e, 'HUDHUD_REVENUE', NULL, 'CREDIT', 100, 'IQD', now(), 1)"
        ), {"e": E})
except IntegrityError as exc:
    assert "party_matches_account_kind" in str(exc), str(exc)
    print("UNSCOPED_CUSTODY_REFUSED")
else:
    raise AssertionError("driver cash custody was posted without a driver")
""",
    )
    assert "UNSCOPED_CUSTODY_REFUSED" in out


def test_an_idempotency_key_cannot_be_reused(applied) -> None:
    """A retried command finds its entry; it does not post a second one."""
    lab, _ = applied
    out = sql(
        lab,
        """
from sqlalchemy.exc import IntegrityError

def post(entry_id):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO finance_journal_entries "
            "(entry_id, reason, occurred_at, currency, total_minor_units, "
            " idempotency_key, created_at) "
            "VALUES (:e, 'COD_CASH_COLLECTED', now(), 'IQD', 100, 'same-key', now())"
        ), {"e": entry_id})
        c.execute(text(
            "INSERT INTO finance_journal_postings "
            "(posting_id, entry_id, account_kind, party_id, side, amount_minor_units, "
            " currency, occurred_at, position) VALUES "
            "(gen_random_uuid(), :e, 'BANK', NULL, 'DEBIT', 100, 'IQD', now(), 0), "
            "(gen_random_uuid(), :e, 'HUDHUD_REVENUE', NULL, 'CREDIT', 100, 'IQD', now(), 1)"
        ), {"e": entry_id})

post("77777777-7777-4777-8777-777777777777")
try:
    post("88888888-8888-4888-8888-888888888888")
except IntegrityError:
    print("DUPLICATE_KEY_REFUSED")
else:
    raise AssertionError("the same idempotency key posted twice")
""",
    )
    assert "DUPLICATE_KEY_REFUSED" in out


# ------------------------------------------------------- the rest of the schema


def test_no_money_column_is_a_float_or_a_numeric(applied) -> None:
    """Exact IQD: integer minor units everywhere, and nothing else anywhere."""
    _, schema = applied
    offenders = [
        f"{table}.{column}: {detail['columns'][column]['type']}"
        for table, detail in schema.items()
        for column in detail["columns"]
        if column.endswith("_minor_units")
        and "INT" not in detail["columns"][column]["type"].upper()
    ]
    assert offenders == []


def test_no_column_in_this_service_is_a_float(applied) -> None:
    _, schema = applied
    offenders = [
        f"{table}.{column}"
        for table, detail in schema.items()
        for column in detail["columns"]
        if "FLOAT" in detail["columns"][column]["type"].upper()
        or "DOUBLE" in detail["columns"][column]["type"].upper()
        or "NUMERIC" in detail["columns"][column]["type"].upper()
    ]
    assert offenders == []


def test_settled_cash_always_names_the_deposit_that_settled_it(applied) -> None:
    """PAY-01, PAY-04 — cash is paid only through a hub cashier."""
    _, schema = applied
    checks = schema["finance_cod_collections"]["check_constraints"]
    assert "ck_finance_settled_cash_names_its_deposit" in checks
    assert "ck_finance_cash_collection_names_the_driver" in checks


def test_a_deposit_decision_is_attributed(applied) -> None:
    _, schema = applied
    checks = schema["finance_deposits"]["check_constraints"]
    assert "ck_finance_deposit_decision_is_attributed" in checks
    assert "ck_finance_deposit_rejection_has_a_reason" in checks
    assert "ck_finance_deposit_has_a_receipt" in checks


def test_a_payout_names_its_destination_unless_collected_in_person(applied) -> None:
    _, schema = applied
    checks = schema["finance_payout_requests"]["check_constraints"]
    assert "ck_finance_payout_names_its_destination" in checks
    assert "ck_finance_payout_decision_is_attributed" in checks


def test_a_reconciliation_outcome_must_match_its_own_figures(applied) -> None:
    """DRV-A13 — an outcome that disagrees with the count explains nothing."""
    _, schema = applied
    checks = schema["finance_route_reconciliations"]["check_constraints"]
    assert "ck_finance_reconciliation_outcome_matches_the_count" in checks
    assert "ck_finance_mismatch_resolution_is_attributed" in checks


def test_the_outbox_and_inbox_are_keyed_the_way_adr_0008_requires(applied) -> None:
    _, schema = applied
    outbox = schema["finance_integration_outbox"]["unique_constraints"]
    assert "uq_finance_outbox_event_id" in outbox
    assert "uq_finance_outbox_aggregate_version" in outbox
    assert (
        "uq_finance_inbox_consumer_event"
        in schema["finance_integration_inbox"]["unique_constraints"]
    )


def test_the_migration_is_reversible() -> None:
    """Including its triggers and functions — a half-dropped guard is worse than none."""
    lab = start_postgres(f"{SERVICE}-down")
    try:
        assert alembic(SERVICE, lab, "upgrade", "head").returncode == 0
        down = alembic(SERVICE, lab, "downgrade", "base")
        assert down.returncode == 0, down.stderr[-4000:]
        assert set(reflect(SERVICE, lab)) - {"alembic_version"} == set()
        left_behind = run_in_service(
            SERVICE,
            """
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["FINANCE_DATABASE_URL"])
with engine.begin() as c:
    rows = c.execute(text(
        "SELECT proname FROM pg_proc WHERE proname LIKE 'finance_%'"
    )).fetchall()
print("FUNCTIONS:" + ",".join(r[0] for r in rows))
""",
            lab,
        )
        assert "FUNCTIONS:\n" in left_behind.stdout or "FUNCTIONS:" in left_behind.stdout
        assert "finance_assert_entry_balances" not in left_behind.stdout
        assert "finance_refuse_ledger_mutation" not in left_behind.stdout
    finally:
        stop_postgres(lab)
