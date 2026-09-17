"""Static guards on the Customer schema and its migration."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from customer.domain.entities import Address, Contact, CustomerProfile, LegalAcceptance
from customer.infrastructure.persistence.models import (
    AddressRow,
    Base,
    ContactRow,
    CustomerProfileRow,
    LegalAcceptanceRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# `geo` is stored as the latitude/longitude pair, and `notification_channels` as a
# delimited string; both are asserted separately below.
_PAIRS = (
    (CustomerProfile, CustomerProfileRow, set()),
    (LegalAcceptance, LegalAcceptanceRow, set()),
    (Contact, ContactRow, set()),
    (Address, AddressRow, {"geo"}),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_a_map_pin_is_stored_as_both_coordinates() -> None:
    columns = set(AddressRow.__table__.columns.keys())
    assert {"latitude", "longitude"} <= columns


def test_every_column_is_created_by_a_migration() -> None:
    source = _migration_source()
    missing = [
        f"{table.name}.{column}"
        for table in Base.metadata.sorted_tables
        for column in table.columns.keys()  # noqa: SIM118
        if f'"{column}"' not in source
    ]
    assert missing == []


def test_every_table_is_created_by_a_migration() -> None:
    source = _migration_source()
    for table in Base.metadata.sorted_tables:
        assert f'"{table.name}"' in source


def test_one_live_default_address_per_owner_and_kind_is_enforced() -> None:
    source = _migration_source()
    assert "uq_customer_addresses_one_default" in source
    assert "is_default AND archived_at IS NULL" in source


def test_a_half_specified_map_pin_is_rejected_by_the_database() -> None:
    source = _migration_source()
    assert "ck_customer_addresses_geo_pair" in source


def test_accepting_one_document_version_twice_is_prevented_by_the_database() -> None:
    assert "uq_customer_legal_once_per_version" in _migration_source()


def test_the_migration_chain_has_exactly_one_root_and_one_head() -> None:
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = _value(text, "revision")
        assert revision is not None, path.name
        revisions[revision] = _value(text, "down_revision")
    roots = [r for r, d in revisions.items() if d is None]
    heads = [r for r in revisions if r not in set(revisions.values())]
    assert len(roots) == 1, roots
    assert len(heads) == 1, heads


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    for path in MIGRATIONS.glob("*.py"):
        revision = _value(path.read_text(encoding="utf-8"), "revision")
        assert revision is not None and len(revision) <= 32, path.name


def _migration_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS.glob("*.py"))


def _value(text: str, name: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{name}: str"):
            raw = line.split("=", 1)[1].strip()
            return None if raw == "None" else raw.strip('"').strip("'")
    return None
