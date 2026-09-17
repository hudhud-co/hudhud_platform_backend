"""W21: Merchant core — application, merchant, stores, team, labels, catalogue, messaging.

Expand-only: this migration creates new tables and touches nothing that exists.

Four rules that are easy to break under concurrency are carried by the database rather
than by service code alone:

* one open merchant application per applicant ("One application at a time")
* one live default pickup point per merchant
* one live team membership per phone number per merchant
* label consumption can never exceed what was issued
* packaging seals are Hudhud-supplied and can never be self-printed
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w21_merchant_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_applications",
        sa.Column("application_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("applicant_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("reference", name="uq_merchant_application_reference"),
    )
    op.create_index(
        "uq_merchant_application_one_open_per_applicant",
        "merchant_applications",
        ["applicant_principal_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('DRAFT', 'SUBMITTED', 'CHANGES_REQUESTED')"),
    )
    op.create_index(
        "ix_merchant_application_status", "merchant_applications", ["status"]
    )

    op.create_table(
        "merchants",
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("merchant_code", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("application_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("merchant_code", name="uq_merchant_code"),
        sa.UniqueConstraint("application_id", name="uq_merchant_application"),
    )
    op.create_index("ix_merchant_owner", "merchants", ["owner_principal_id"])

    op.create_table(
        "merchant_standing_policies",
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "open_box_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "photo_documentation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "hudhud_packaging_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("delivery_fee_payer", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "merchant_stores",
        sa.Column("store_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("governorate", sa.String(length=32), nullable=False),
        sa.Column("address_line", sa.String(length=512), nullable=False),
        sa.Column("area", sa.String(length=160), nullable=True),
        sa.Column("landmark", sa.String(length=256), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column(
            "is_default_pickup", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name="ck_merchant_store_geo_pair",
        ),
    )
    op.create_index(
        "uq_merchant_store_one_default_pickup",
        "merchant_stores",
        ["merchant_id"],
        unique=True,
        postgresql_where=sa.text("is_default_pickup AND archived_at IS NULL"),
    )
    op.create_index("ix_merchant_store_merchant", "merchant_stores", ["merchant_id"])

    op.create_table(
        "merchant_team_memberships",
        sa.Column("membership_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("invited_phone", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "store_ids", postgresql.ARRAY(sa.Uuid(as_uuid=True)), nullable=False
        ),
        sa.Column("member_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status <> 'ACTIVE' OR member_principal_id IS NOT NULL",
            name="ck_merchant_team_active_has_principal",
        ),
        sa.CheckConstraint(
            "cardinality(store_ids) >= 1", name="ck_merchant_team_has_branch"
        ),
    )
    op.create_index(
        "uq_merchant_team_live_membership",
        "merchant_team_memberships",
        ["merchant_id", "invited_phone"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'ACTIVE')"),
    )
    op.create_index(
        "ix_merchant_team_principal",
        "merchant_team_memberships",
        ["member_principal_id"],
    )
    op.create_index(
        "ix_merchant_team_phone", "merchant_team_memberships", ["invited_phone"]
    )

    op.create_table(
        "merchant_printer_authorizations",
        sa.Column("authorization_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("printer_serial", sa.String(length=64), nullable=False),
        sa.Column("stock_reference", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_merchant_printer_merchant",
        "merchant_printer_authorizations",
        ["merchant_id", "status"],
    )

    op.create_table(
        "merchant_label_stock",
        sa.Column("allocation_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("batch_reference", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("stock_kind", sa.String(length=24), nullable=False),
        sa.Column("label_count", sa.Integer(), nullable=False),
        sa.Column("consumed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("printer_authorization_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "merchant_id", "batch_reference", name="uq_merchant_label_batch"
        ),
        sa.CheckConstraint("label_count >= 1", name="ck_merchant_label_count_positive"),
        sa.CheckConstraint(
            "consumed_count >= 0 AND consumed_count <= label_count",
            name="ck_merchant_label_consumed_within_allocation",
        ),
        sa.CheckConstraint(
            "source <> 'MERCHANT_SELF_PRINTED' OR printer_authorization_id IS NOT NULL",
            name="ck_merchant_label_self_print_authorized",
        ),
        sa.CheckConstraint(
            "stock_kind <> 'PACKAGING_SEAL' OR source = 'HUDHUD_PREPRINTED'",
            name="ck_merchant_seal_stock_is_hudhud_supplied",
        ),
    )
    op.create_index(
        "ix_merchant_label_stock_merchant", "merchant_label_stock", ["merchant_id"]
    )

    op.create_table(
        "merchant_product_categories",
        sa.Column("category_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "uq_merchant_category_name",
        "merchant_product_categories",
        ["merchant_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "merchant_products",
        sa.Column("product_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_merchant_products_merchant", "merchant_products", ["merchant_id"])
    op.create_index("ix_merchant_products_category", "merchant_products", ["category_id"])

    op.create_table(
        "merchant_integration_outbox",
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
        sa.UniqueConstraint("event_id", name="uq_merchant_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_merchant_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_merchant_outbox_pending",
        "merchant_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "merchant_integration_inbox",
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
            "consumer_name", "event_id", name="uq_merchant_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_merchant_inbox_status",
        "merchant_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_table("merchant_integration_inbox")
    op.drop_table("merchant_integration_outbox")
    op.drop_table("merchant_products")
    op.drop_table("merchant_product_categories")
    op.drop_table("merchant_label_stock")
    op.drop_table("merchant_printer_authorizations")
    op.drop_table("merchant_team_memberships")
    op.drop_table("merchant_stores")
    op.drop_table("merchant_standing_policies")
    op.drop_table("merchants")
    op.drop_table("merchant_applications")
