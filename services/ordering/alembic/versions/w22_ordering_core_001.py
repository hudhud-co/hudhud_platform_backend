"""W22: Ordering core — orders, shipment requests, goods taxonomy, tariff, messaging.

Expand-only: this migration creates new tables and touches nothing that exists.

Four v6.3 rules are carried by the database rather than by service code alone, because
each of them is one concurrent request away from being violated:

* a regular customer's parcel can never carry COD (p.8, Confirmed decision);
* a parcel always has a description, so it is never an unknown item (p.12);
* a tracking code is unique, and a live label belongs to exactly one parcel;
* money is an integer with an explicit currency, or absent — never a bare number.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w22_ordering_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("order_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("sender_kind", sa.String(length=16), nullable=False),
        sa.Column("sender_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sender_merchant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sender_store_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("reference", name="uq_order_reference"),
        sa.CheckConstraint(
            "(sender_kind = 'MERCHANT') = (sender_merchant_id IS NOT NULL)",
            name="ck_order_merchant_sender_has_merchant",
        ),
    )
    op.create_index("ix_order_sender", "orders", ["sender_principal_id"])
    op.create_index("ix_order_merchant", "orders", ["sender_merchant_id"])

    op.create_table(
        "shipment_requests",
        sa.Column("request_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("order_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=24), nullable=False),
        sa.Column("sender_kind", sa.String(length=16), nullable=False),
        sa.Column("sender_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sender_merchant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sender_store_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("receiver_phone", sa.String(length=32), nullable=False),
        sa.Column("receiver_governorate", sa.String(length=32), nullable=False),
        sa.Column("receiver_name", sa.String(length=160), nullable=True),
        sa.Column("receiver_address_line", sa.String(length=512), nullable=True),
        sa.Column("receiver_landmark", sa.String(length=256), nullable=True),
        sa.Column("receiver_latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("receiver_longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("goods_category_code", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("length_cm", sa.Integer(), nullable=True),
        sa.Column("width_cm", sa.Integer(), nullable=True),
        sa.Column("height_cm", sa.Integer(), nullable=True),
        sa.Column("payment_terms", sa.String(length=24), nullable=False),
        sa.Column("cod_amount_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("cod_currency", sa.String(length=3), nullable=True),
        sa.Column(
            "open_box_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "photo_documentation", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "hudhud_packaging", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("delivery_fee_payer", sa.String(length=16), nullable=False),
        sa.Column("label_code", sa.String(length=32), nullable=True),
        sa.Column("label_linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pickup_store_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pickup_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pickup_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_courier_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("delivery_fee_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("packaging_fee_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("fee_currency", sa.String(length=3), nullable=True),
        sa.Column("tariff_reference", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=32), nullable=True),
        sa.Column("custody_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("courier_at_receiver_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tracking_code", name="uq_shipment_request_tracking_code"),
        sa.CheckConstraint(
            "(sender_kind = 'MERCHANT') = (sender_merchant_id IS NOT NULL)",
            name="ck_shipment_request_merchant_sender_has_merchant",
        ),
        sa.CheckConstraint(
            "payment_terms <> 'CASH_ON_DELIVERY' OR sender_kind = 'MERCHANT'",
            name="ck_shipment_request_no_cod_for_customers",
        ),
        sa.CheckConstraint(
            "(cod_amount_minor_units IS NULL) = (cod_currency IS NULL)",
            name="ck_shipment_request_cod_amount_pair",
        ),
        sa.CheckConstraint(
            "cod_amount_minor_units IS NULL OR cod_amount_minor_units >= 0",
            name="ck_shipment_request_cod_not_negative",
        ),
        sa.CheckConstraint(
            "payment_terms <> 'CASH_ON_DELIVERY' OR cod_amount_minor_units IS NOT NULL",
            name="ck_shipment_request_cod_has_amount",
        ),
        sa.CheckConstraint(
            "length(btrim(description)) > 0", name="ck_shipment_request_described"
        ),
        sa.CheckConstraint(
            "(receiver_latitude IS NULL) = (receiver_longitude IS NULL)",
            name="ck_shipment_request_geo_pair",
        ),
    )
    op.create_index(
        "uq_shipment_request_label_code",
        "shipment_requests",
        ["label_code"],
        unique=True,
        postgresql_where=sa.text("label_code IS NOT NULL AND status <> 'CANCELLED'"),
    )
    op.create_index("ix_shipment_request_order", "shipment_requests", ["order_id"])
    op.create_index(
        "ix_shipment_request_sender", "shipment_requests", ["sender_principal_id"]
    )
    op.create_index(
        "ix_shipment_request_merchant", "shipment_requests", ["sender_merchant_id"]
    )
    op.create_index("ix_shipment_request_status", "shipment_requests", ["status"])

    op.create_table(
        "goods_categories",
        sa.Column("code", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("hint", sa.String(length=256), nullable=True),
        sa.Column("restriction_note", sa.String(length=256), nullable=True),
        sa.Column("prohibited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "tariff_rates",
        sa.Column("tariff_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("origin_governorate", sa.String(length=32), nullable=False),
        sa.Column("destination_governorate", sa.String(length=32), nullable=False),
        sa.Column("delivery_fee_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("packaging_fee_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "delivery_fee_minor_units >= 0 AND packaging_fee_minor_units >= 0",
            name="ck_tariff_fees_not_negative",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_tariff_window_ordered",
        ),
    )
    op.create_index(
        "ix_tariff_route",
        "tariff_rates",
        ["origin_governorate", "destination_governorate", "effective_from"],
    )

    op.create_table(
        "ordering_integration_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column(
            "payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_owner", sa.String(length=128), nullable=True),
        sa.Column("processing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_ordering_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_ordering_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_ordering_outbox_pending",
        "ordering_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "ordering_integration_inbox",
        sa.Column("inbox_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "consumer_name", "event_id", name="uq_ordering_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_ordering_inbox_status",
        "ordering_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_table("ordering_integration_inbox")
    op.drop_table("ordering_integration_outbox")
    op.drop_table("tariff_rates")
    op.drop_table("goods_categories")
    op.drop_table("shipment_requests")
    op.drop_table("orders")
