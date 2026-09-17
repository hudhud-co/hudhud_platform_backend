"""Prove the Customer migration against a real, disposable PostgreSQL 16.

The migration's own header names the two rules it leans on the database to hold under
concurrency: at most one live default address per (owner, kind), and one acceptance row
per (principal, document, version). Both are enforced by a partial unique index and a
plain unique constraint respectively — neither of which a static read of the migration
text can tell apart from one that enforces nothing. These tests apply the migration for
real and then try to write the rows those rules exist to refuse.

The third rule is the geo pair: a check constraint saying a pin needs both coordinates or
neither. A half-set pin is how an address renders on a map at the equator.
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

SERVICE = "customer"
MODELS = "customer.infrastructure.persistence.models"
EXPECTED_HEAD = "w20b_customer_core_001"

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER_OWNER = "22222222-2222-4222-8222-222222222222"


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
    script = f"""
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["CUSTOMER_DATABASE_URL"])
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


def _acceptance(
    acceptance_id: str,
    *,
    principal_id: str = OWNER,
    kind: str = "TERMS",
    document_version: str = "v1",
) -> str:
    return f"""
        c.execute(text(
            "INSERT INTO customer_legal_acceptances "
            "(acceptance_id, principal_id, kind, document_version, accepted_at) "
            "VALUES (:id, :pid, :kind, :ver, now())"
        ), {{"id": {acceptance_id!r}, "pid": {principal_id!r},
             "kind": {kind!r}, "ver": {document_version!r}}})
"""


def _address(
    address_id: str,
    *,
    owner_principal_id: str = OWNER,
    kind: str = "PICKUP",
    is_default: str = "true",
    archived: bool = False,
    latitude: str = "NULL",
    longitude: str = "NULL",
) -> str:
    archived_at = "now()" if archived else "NULL"
    return f"""
        c.execute(text(
            "INSERT INTO customer_addresses "
            "(address_id, owner_principal_id, kind, governorate, line, is_default, "
            " latitude, longitude, archived_at, version) "
            f"VALUES (:id, :owner, :kind, 'Baghdad', 'a line', {is_default}, "
            f" {latitude}, {longitude}, {archived_at}, 1)"
        ), {{"id": {address_id!r}, "owner": {owner_principal_id!r}, "kind": {kind!r}}})
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


# ------------------------------------------------- SEC-04 — accepting is not a counter


def test_accepting_one_document_version_twice_is_impossible_in_postgres(applied) -> None:
    """A second row is a replay, and a replay that lands looks like a second consent."""
    lab, _ = applied
    output = attempt(
        lab,
        _acceptance("a0000001-0000-4000-8000-000000000001")
        + _acceptance("a0000002-0000-4000-8000-000000000002"),
    )
    assert "ACCEPTED" not in output
    assert "uq_customer_legal_once_per_version" in output


def test_a_new_document_version_must_be_acceptable(applied) -> None:
    """SEC-04 enforces the *current* version; the index must not freeze the first one."""
    lab, _ = applied
    output = attempt(
        lab,
        _acceptance("a0000003-0000-4000-8000-000000000003", document_version="v2")
        + _acceptance("a0000004-0000-4000-8000-000000000004", document_version="v3"),
    )
    assert "ACCEPTED" in output


def test_terms_and_privacy_are_accepted_independently(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _acceptance(
            "a0000005-0000-4000-8000-000000000005", kind="PRIVACY", document_version="v1"
        )
        + _acceptance(
            "a0000006-0000-4000-8000-000000000006", kind="NOTIFICATIONS", document_version="v1"
        ),
    )
    assert "ACCEPTED" in output


# ------------------------------------------------- one live default address


def test_two_live_defaults_for_one_owner_and_kind_are_impossible(applied) -> None:
    """Two defaults make "the pickup address" ambiguous at the moment a courier is sent."""
    lab, _ = applied
    output = attempt(
        lab,
        _address("d0000001-0000-4000-8000-000000000001")
        + _address("d0000002-0000-4000-8000-000000000002"),
    )
    assert "ACCEPTED" not in output
    assert "uq_customer_addresses_one_default" in output


def test_an_archived_default_does_not_block_the_next_one(applied) -> None:
    """The index is partial on `archived_at IS NULL`: history must not pin the default."""
    lab, _ = applied
    output = attempt(
        lab,
        _address("d0000003-0000-4000-8000-000000000003", kind="DROPOFF", archived=True)
        + _address("d0000004-0000-4000-8000-000000000004", kind="DROPOFF"),
    )
    assert "ACCEPTED" in output


def test_a_pickup_default_and_a_delivery_default_coexist(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _address("d0000005-0000-4000-8000-000000000005", owner_principal_id=OTHER_OWNER)
        + _address(
            "d0000006-0000-4000-8000-000000000006",
            owner_principal_id=OTHER_OWNER,
            kind="DELIVERY",
        ),
    )
    assert "ACCEPTED" in output


def test_an_owner_may_keep_many_non_default_addresses(applied) -> None:
    """The index is partial on `is_default` too — only defaults are constrained."""
    lab, _ = applied
    output = attempt(
        lab,
        _address(
            "d0000007-0000-4000-8000-000000000007", kind="OTHER", is_default="false"
        )
        + _address(
            "d0000008-0000-4000-8000-000000000008", kind="OTHER", is_default="false"
        ),
    )
    assert "ACCEPTED" in output


# ------------------------------------------------- a pin needs both coordinates


def test_a_latitude_without_a_longitude_is_refused_by_postgres(applied) -> None:
    """Half a pin renders on the map somewhere nobody chose."""
    lab, _ = applied
    output = attempt(
        lab,
        _address(
            "d0000009-0000-4000-8000-000000000009",
            kind="GEO_LAT",
            latitude="33.312805",
        ),
    )
    assert "ACCEPTED" not in output
    assert "ck_customer_addresses_geo_pair" in output


def test_a_longitude_without_a_latitude_is_refused_by_postgres(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _address(
            "d000000a-0000-4000-8000-00000000000a",
            kind="GEO_LON",
            longitude="44.361488",
        ),
    )
    assert "ACCEPTED" not in output
    assert "ck_customer_addresses_geo_pair" in output


def test_a_complete_pin_and_no_pin_at_all_are_both_accepted(applied) -> None:
    lab, _ = applied
    output = attempt(
        lab,
        _address(
            "d000000b-0000-4000-8000-00000000000b",
            kind="GEO_BOTH",
            latitude="33.312805",
            longitude="44.361488",
        )
        + _address("d000000c-0000-4000-8000-00000000000c", kind="GEO_NONE"),
    )
    assert "ACCEPTED" in output


def test_the_stored_pin_keeps_its_six_decimal_places(applied) -> None:
    """NUMERIC(9,6) is about eleven centimetres; a narrower scale moves the door."""
    lab, _ = applied
    output = sql(
        lab,
        """
with engine.begin() as c:
    row = c.execute(text(
        "SELECT latitude, longitude FROM customer_addresses WHERE kind = 'GEO_BOTH'"
    )).one()
print("PIN:" + str(row[0]) + "," + str(row[1]))
""",
    )
    assert "PIN:33.312805,44.361488" in output


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
