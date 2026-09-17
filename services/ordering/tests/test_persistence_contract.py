"""Static guards on the Ordering schema and its migration."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ordering.domain.entities import GoodsCategory, Order, ShipmentRequest, TariffRate
from ordering.domain.messaging import InboxRecord, OutboxRecord
from ordering.infrastructure.persistence.models import (
    Base,
    GoodsCategoryRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    OrderRow,
    ShipmentRequestRow,
    TariffRateRow,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Composite value objects are flattened into columns and asserted separately below.
_PAIRS = (
    (Order, OrderRow, {"sender"}),
    (
        ShipmentRequest,
        ShipmentRequestRow,
        {"sender", "receiver", "measurements", "add_ons", "cod_amount",
         "delivery_fee", "packaging_fee"},
    ),
    (GoodsCategory, GoodsCategoryRow, set()),
    (TariffRate, TariffRateRow, {"delivery_fee", "packaging_fee"}),
    (OutboxRecord, IntegrationOutboxRow, set()),
    (InboxRecord, IntegrationInboxRow, set()),
)


def test_every_entity_field_has_a_column() -> None:
    for entity_cls, row_cls, exempt in _PAIRS:
        fields = {f.name for f in dataclasses.fields(entity_cls)} - exempt
        columns = set(row_cls.__table__.columns.keys())
        assert fields - columns == set(), (entity_cls.__name__, fields - columns)


def test_the_sender_is_flattened_into_four_columns() -> None:
    columns = set(ShipmentRequestRow.__table__.columns.keys())
    assert {
        "sender_kind",
        "sender_principal_id",
        "sender_merchant_id",
        "sender_store_id",
    } <= columns


def test_the_receiver_is_flattened_and_keeps_both_coordinates() -> None:
    columns = set(ShipmentRequestRow.__table__.columns.keys())
    assert {
        "receiver_phone",
        "receiver_governorate",
        "receiver_name",
        "receiver_address_line",
        "receiver_latitude",
        "receiver_longitude",
    } <= columns


def test_measurements_are_flattened_and_all_nullable() -> None:
    """v6.3 p.12 made weight and size optional, so the schema must allow their absence."""
    for name in ("weight_grams", "length_cm", "width_cm", "height_cm"):
        assert ShipmentRequestRow.__table__.columns[name].nullable is True


def test_a_description_column_is_not_nullable() -> None:
    assert ShipmentRequestRow.__table__.columns["description"].nullable is False


# --------------------------------------------------- money in the schema


def test_money_is_stored_as_integer_minor_units_with_its_currency() -> None:
    """A numeric with an assumed scale, or a float, would lose exactness."""
    for table, amount, currency in (
        (ShipmentRequestRow, "cod_amount_minor_units", "cod_currency"),
        (TariffRateRow, "delivery_fee_minor_units", "currency"),
        (TariffRateRow, "packaging_fee_minor_units", "currency"),
    ):
        column = table.__table__.columns[amount]
        assert "INT" in str(column.type).upper(), (table.__name__, amount, column.type)
        assert currency in table.__table__.columns


def test_no_money_column_is_a_float_or_a_numeric() -> None:
    money_columns = [
        column
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if "minor_units" in column.name or "fee" in column.name
    ]
    assert money_columns
    for column in money_columns:
        rendered = str(column.type).upper()
        assert "FLOAT" not in rendered and "NUMERIC" not in rendered, column.name


# --------------------------------------------------- invariants in the schema


def test_a_customer_parcel_can_never_carry_cod_in_the_database() -> None:
    """v6.3 p.8 Confirmed decision, enforced below the service layer too."""
    source = _migration_source()
    assert "ck_shipment_request_no_cod_for_customers" in source
    assert "payment_terms <> 'CASH_ON_DELIVERY' OR sender_kind = 'MERCHANT'" in source


def test_a_cod_parcel_must_carry_an_amount() -> None:
    assert "ck_shipment_request_cod_has_amount" in _migration_source()


def test_an_amount_and_its_currency_are_present_or_absent_together() -> None:
    assert "ck_shipment_request_cod_amount_pair" in _migration_source()


def test_a_parcel_is_never_registered_without_a_description() -> None:
    source = _migration_source()
    assert "ck_shipment_request_described" in source
    assert "length(btrim(description)) > 0" in source


def test_a_merchant_sender_always_names_its_merchant() -> None:
    source = _migration_source()
    assert "ck_order_merchant_sender_has_merchant" in source
    assert "ck_shipment_request_merchant_sender_has_merchant" in source


def test_a_tracking_code_is_unique() -> None:
    assert "uq_shipment_request_tracking_code" in _migration_source()


def test_a_live_label_belongs_to_exactly_one_parcel() -> None:
    source = _migration_source()
    assert "uq_shipment_request_label_code" in source
    assert "label_code IS NOT NULL AND status <> 'CANCELLED'" in source


def test_a_tariff_window_cannot_end_before_it_starts() -> None:
    assert "ck_tariff_window_ordered" in _migration_source()


def test_one_event_per_aggregate_version() -> None:
    assert "uq_ordering_outbox_aggregate_version" in _migration_source()


def test_the_inbox_is_keyed_by_consumer_and_event() -> None:
    assert "uq_ordering_inbox_consumer_event" in _migration_source()


# --------------------------------------------------- migration hygiene


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


def test_the_migration_is_expand_only() -> None:
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
