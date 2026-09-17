"""Prove the Identity migration against a real, disposable PostgreSQL 16.

Identity's schema carries three of the platform's load-bearing rules as *partial* unique
indexes — one live OTP per phone, one live grant per (principal, role, scope) globally,
and the same scoped. A partial index is precisely what a static check over the migration
text cannot verify: the predicate is a string until PostgreSQL parses it, and an index
whose `WHERE` clause is subtly wrong still creates successfully and simply stops
enforcing anything. These tests apply the migration for real and then try to write the
rows those indexes exist to refuse.

The privacy claim in `identity.infrastructure.persistence.models` — that no column
stores a phone number, OTP code, session token or device id in recoverable form — is
checked here against the schema PostgreSQL actually built, not against the models.
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

SERVICE = "identity"
MODELS = "identity.infrastructure.persistence.models"
EXPECTED_HEAD = "w20a_identity_core_001"

PRINCIPAL = "11111111-1111-4111-8111-111111111111"
OTHER_PRINCIPAL = "22222222-2222-4222-8222-222222222222"
MERCHANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MERCHANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


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


def sql(lab, statements: str) -> str:
    """Run raw SQL in the service's venv and hand back what it printed."""
    script = f"""
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["IDENTITY_DATABASE_URL"])
{statements}
"""
    result = run_in_service(SERVICE, script, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def attempt(lab, body: str) -> str:
    """Try to write rows; report whether PostgreSQL accepted or which rule refused."""
    return sql(
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


def _principal(principal_id: str, phone_hash: str) -> str:
    return f"""
        c.execute(text(
            "INSERT INTO identity_principals "
            "(principal_id, phone_hash, phone_last4, status, created_at, version) "
            "VALUES (:id, :ph, '1234', 'ACTIVE', now(), 1)"
        ), {{"id": {principal_id!r}, "ph": {phone_hash!r}}})
"""


def _otp(challenge_id: str, phone_hash: str, *, consumed: bool = False) -> str:
    consumed_at = "now()" if consumed else "NULL"
    return f"""
        c.execute(text(
            "INSERT INTO identity_otp_challenges "
            "(challenge_id, phone_hash, code_hash, purpose, issued_at, expires_at, "
            " max_attempts, failed_attempts, consumed_at, version) "
            "VALUES (:id, :ph, 'x', 'SIGN_IN', now(), now() + interval '5 minutes', "
            f" 5, 0, {consumed_at}, 1)"
        ), {{"id": {challenge_id!r}, "ph": {phone_hash!r}}})
"""


def _session(session_id: str, token_hash: str, principal_id: str = PRINCIPAL) -> str:
    return f"""
        c.execute(text(
            "INSERT INTO identity_sessions "
            "(session_id, principal_id, token_hash, issued_at, expires_at, version) "
            "VALUES (:id, :pid, :th, now(), now() + interval '1 day', 1)"
        ), {{"id": {session_id!r}, "pid": {principal_id!r}, "th": {token_hash!r}}})
"""


def _grant(
    grant_id: str,
    *,
    principal_id: str = PRINCIPAL,
    role: str = "OPERATIONS",
    scope_kind: str = "GLOBAL",
    scope_id: str | None = None,
    revoked: bool = False,
) -> str:
    revoked_at = "now()" if revoked else "NULL"
    scope = repr(scope_id) if scope_id is not None else "None"
    return f"""
        c.execute(text(
            "INSERT INTO identity_role_grants "
            "(grant_id, principal_id, role, scope_kind, scope_id, granted_at, "
            " granted_by, revoked_at) "
            f"VALUES (:id, :pid, :role, :sk, :sid, now(), 'ops-1', {revoked_at})"
        ), {{"id": {grant_id!r}, "pid": {principal_id!r}, "role": {role!r},
             "sk": {scope_kind!r}, "sid": {scope}}})
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
    missing = sorted(set(model_tables(SERVICE, MODELS)) - set(schema))
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
    assert sorted(set(schema) - expected - {"alembic_version"}) == []


# ------------------------------------------------- SEC-01/SEC-02 — one live OTP


def test_two_live_codes_for_one_phone_are_impossible_in_postgres(applied) -> None:
    """Requesting a new code invalidates the previous one; two open rows would let
    an attacker keep a harvested code alive alongside the one the user is reading."""
    lab, _ = applied
    output = attempt(
        lab,
        _otp("c0000001-0000-4000-8000-000000000001", "phone-hash-live")
        + _otp("c0000002-0000-4000-8000-000000000002", "phone-hash-live"),
    )
    assert "ACCEPTED" not in output
    assert "uq_identity_otp_one_open_per_phone" in output


def test_a_consumed_code_does_not_block_the_next_one(applied) -> None:
    """The index is partial for a reason: history must not stop a user signing in again."""
    lab, _ = applied
    output = attempt(
        lab,
        _otp("c0000003-0000-4000-8000-000000000003", "phone-hash-reuse", consumed=True)
        + _otp("c0000004-0000-4000-8000-000000000004", "phone-hash-reuse"),
    )
    assert "ACCEPTED" in output


def test_two_phones_may_each_hold_a_live_code(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _otp("c0000005-0000-4000-8000-000000000005", "phone-hash-a")
        + _otp("c0000006-0000-4000-8000-000000000006", "phone-hash-b"),
    )
    assert "ACCEPTED" in output


# ------------------------------------------------- one principal per phone


def test_one_phone_cannot_produce_two_principals(applied) -> None:
    """Two principals on one phone is an account-takeover primitive, not a duplicate row."""
    lab, _ = applied
    output = attempt(
        lab,
        _principal(PRINCIPAL, "phone-hash-unique")
        + _principal(OTHER_PRINCIPAL, "phone-hash-unique"),
    )
    assert "ACCEPTED" not in output
    assert "uq_identity_principals_phone_hash" in output


# ------------------------------------------------- session tokens


def test_two_sessions_cannot_share_a_token(applied) -> None:
    """A colliding token hash would make one bearer token resolve to two principals."""
    lab, _ = applied
    setup = _principal("33333333-3333-4333-8333-333333333333", "phone-hash-session")
    assert "ACCEPTED" in attempt(lab, setup)
    output = attempt(
        lab,
        _session(
            "5e551011-0000-4000-8000-000000000001",
            "token-hash-collide",
            "33333333-3333-4333-8333-333333333333",
        )
        + _session(
            "5e551011-0000-4000-8000-000000000002",
            "token-hash-collide",
            "33333333-3333-4333-8333-333333333333",
        ),
    )
    assert "ACCEPTED" not in output
    assert "uq_identity_sessions_token_hash" in output


# ------------------------------------------------- SEC-09 — one live grant


def test_a_role_cannot_be_granted_twice_globally(applied) -> None:
    """Two live rows make revocation ambiguous: revoking one leaves the role held."""
    lab, _ = applied
    output = attempt(
        lab,
        _grant("91111111-0000-4000-8000-000000000001")
        + _grant("91111111-0000-4000-8000-000000000002"),
    )
    assert "ACCEPTED" not in output
    assert "uq_identity_role_grant_live_global" in output


def test_a_revoked_grant_does_not_block_regranting(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _grant("92222222-0000-4000-8000-000000000001", role="SUPPORT", revoked=True)
        + _grant("92222222-0000-4000-8000-000000000002", role="SUPPORT"),
    )
    assert "ACCEPTED" in output


def test_the_same_scoped_role_cannot_be_granted_twice(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _grant(
            "93333333-0000-4000-8000-000000000001",
            role="MERCHANT_MEMBER",
            scope_kind="MERCHANT",
            scope_id=MERCHANT_A,
        )
        + _grant(
            "93333333-0000-4000-8000-000000000002",
            role="MERCHANT_MEMBER",
            scope_kind="MERCHANT",
            scope_id=MERCHANT_A,
        ),
    )
    assert "ACCEPTED" not in output
    assert "uq_identity_role_grant_live_scoped" in output


def test_one_member_may_hold_the_same_role_at_two_merchants(applied) -> None:
    """SEC-05 — one principal, several stores. The scoped index must not prevent it."""
    lab, _ = applied
    output = attempt(
        lab,
        _grant(
            "94444444-0000-4000-8000-000000000001",
            principal_id=OTHER_PRINCIPAL,
            role="MERCHANT_MEMBER",
            scope_kind="MERCHANT",
            scope_id=MERCHANT_A,
        )
        + _grant(
            "94444444-0000-4000-8000-000000000002",
            principal_id=OTHER_PRINCIPAL,
            role="MERCHANT_MEMBER",
            scope_kind="MERCHANT",
            scope_id=MERCHANT_B,
        ),
    )
    assert "ACCEPTED" in output


def test_a_global_grant_and_a_scoped_grant_of_one_role_coexist(applied) -> None:
    """The two partial indexes are disjoint by `scope_id IS NULL`; neither may catch
    the other's rows, or an operator could never also be scoped to a hub."""
    lab, _ = applied
    output = attempt(
        lab,
        _grant("95555555-0000-4000-8000-000000000001", role="HUB_OPERATOR")
        + _grant(
            "95555555-0000-4000-8000-000000000002",
            role="HUB_OPERATOR",
            scope_kind="HUB",
            scope_id=MERCHANT_A,
        ),
    )
    assert "ACCEPTED" in output


# ------------------------------------------------- the privacy claim, in the schema


def test_no_column_in_postgres_could_hold_a_phone_number(applied) -> None:
    """`phone_last4` is the only fragment; everything else is a keyed digest."""
    _, schema = applied
    recoverable = [
        f"{table}.{column}"
        for table, definition in schema.items()
        for column in definition["columns"]
        if "phone" in column and column not in {"phone_hash", "phone_last4"}
    ]
    assert recoverable == []


def test_no_column_in_postgres_could_hold_a_code_token_or_device_id(applied) -> None:
    _, schema = applied
    recoverable = [
        f"{table}.{column}"
        for table, definition in schema.items()
        for column in definition["columns"]
        if ("code" in column or "token" in column or "device" in column)
        and not column.endswith("_hash")
    ]
    assert recoverable == []


def test_every_secret_column_is_sized_for_a_digest_not_a_secret(applied) -> None:
    """A widened column is how a hash column quietly starts holding the plaintext."""
    _, schema = applied
    wrong = {
        f"{table}.{column}": definition["columns"][column]["type"]
        for table, definition in schema.items()
        for column in definition["columns"]
        if column.endswith("_hash")
        and definition["columns"][column]["type"] != "VARCHAR(64)"
    }
    assert wrong == {}


# ------------------------------------------------- reversibility


def test_the_migration_is_reversible() -> None:
    """A migration that cannot be undone cannot be rolled back in an incident."""
    lab = start_postgres(f"{SERVICE}-down")
    try:
        assert alembic(SERVICE, lab, "upgrade", "head").returncode == 0
        down = alembic(SERVICE, lab, "downgrade", "base")
        assert down.returncode == 0, down.stderr[-4000:]
        assert set(reflect(SERVICE, lab)) - {"alembic_version"} == set()
    finally:
        stop_postgres(lab)
