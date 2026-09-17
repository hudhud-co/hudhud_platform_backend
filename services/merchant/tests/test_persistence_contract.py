"""Static guards on the Merchant schema and its migration.

These run without a database. They catch the class of mistake that is otherwise only
found in production: a field added to an entity but never to the table, or an invariant
enforced in Python and silently absent from the schema.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from merchant.domain.entities import (
    LabelStockAllocation,
    Merchant,
    MerchantApplication,
    PrinterAuthorization,
    Product,
    ProductCategory,
    StandingShipmentPolicy,
    Store,
    TeamMembership,
)
from merchant.domain.messaging import InboxRecord, OutboxRecord
from merchant.infrastructure.persistence.models import (
    Base,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    LabelStockRow,
    MerchantApplicationRow,
    MerchantRow,
    PrinterAuthorizationRow,
    ProductCategoryRow,
    ProductRow,
    StandingPolicyRow,
    StoreRow,
    TeamMembershipRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# `geo` is stored as the latitude/longitude pair and asserted separately below.
_PAIRS = (
    (MerchantApplication, MerchantApplicationRow, set()),
    (Merchant, MerchantRow, set()),
    (StandingShipmentPolicy, StandingPolicyRow, set()),
    (Store, StoreRow, {"geo"}),
    (TeamMembership, TeamMembershipRow, set()),
    (PrinterAuthorization, PrinterAuthorizationRow, set()),
    (LabelStockAllocation, LabelStockRow, set()),
    (ProductCategory, ProductCategoryRow, set()),
    (Product, ProductRow, set()),
    (OutboxRecord, IntegrationOutboxRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_a_store_map_pin_is_stored_as_both_coordinates() -> None:
    assert {"latitude", "longitude"} <= set(StoreRow.__table__.columns.keys())


def test_every_table_is_created_by_a_migration() -> None:
    source = _migration_source()
    for table in Base.metadata.sorted_tables:
        assert f'"{table.name}"' in source, table.name


def test_every_column_is_created_by_a_migration() -> None:
    source = _migration_source()
    missing = [
        f"{table.name}.{column}"
        for table in Base.metadata.sorted_tables
        for column in table.columns.keys()  # noqa: SIM118
        if f'"{column}"' not in source
    ]
    assert missing == []


# --------------------------------------------------- invariants in the schema


def test_one_open_application_per_applicant_is_enforced_by_the_database() -> None:
    source = _migration_source()
    assert "uq_merchant_application_one_open_per_applicant" in source
    assert "status IN ('DRAFT', 'SUBMITTED', 'CHANGES_REQUESTED')" in source


def test_one_live_default_pickup_point_per_merchant_is_enforced() -> None:
    source = _migration_source()
    assert "uq_merchant_store_one_default_pickup" in source
    assert "is_default_pickup AND archived_at IS NULL" in source


def test_one_live_membership_per_number_per_merchant_is_enforced() -> None:
    source = _migration_source()
    assert "uq_merchant_team_live_membership" in source
    assert "status IN ('PENDING', 'ACTIVE')" in source


def test_an_active_membership_must_name_a_principal() -> None:
    assert "ck_merchant_team_active_has_principal" in _migration_source()


def test_a_membership_must_cover_at_least_one_branch() -> None:
    assert "ck_merchant_team_has_branch" in _migration_source()


def test_label_consumption_cannot_exceed_the_allocation() -> None:
    source = _migration_source()
    assert "ck_merchant_label_consumed_within_allocation" in source
    assert "consumed_count <= label_count" in source


def test_self_printed_stock_must_name_its_authorization() -> None:
    assert "ck_merchant_label_self_print_authorized" in _migration_source()


def test_a_merchant_code_is_unique() -> None:
    assert "uq_merchant_code" in _migration_source()


def test_one_event_per_aggregate_version() -> None:
    assert "uq_merchant_outbox_aggregate_version" in _migration_source()


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert "uq_merchant_inbox_consumer_event" in _migration_source()


def test_a_half_specified_store_pin_is_rejected_by_the_database() -> None:
    assert "ck_merchant_store_geo_pair" in _migration_source()


# --------------------------------------------------- migration hygiene


def test_the_migration_is_expand_only() -> None:
    """A first migration must not drop or alter anything that already exists."""
    source = _migration_source()
    upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
    for forbidden in ("op.drop_", "op.alter_column", "op.execute("):
        assert forbidden not in upgrade, forbidden


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
