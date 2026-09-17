"""W20-B: Customer core — profile, legal acceptance, contacts and addresses.

Two partial unique indexes carry product rules that are otherwise easy to violate under
concurrency: at most one live default address per (owner, kind), and one acceptance row
per (principal, document, version).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w20b_customer_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_profiles",
        sa.Column("principal_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("notification_channels", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "customer_legal_acceptances",
        sa.Column("acceptance_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("document_version", sa.String(length=32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "principal_id",
            "kind",
            "document_version",
            name="uq_customer_legal_once_per_version",
        ),
    )

    op.create_table(
        "customer_contacts",
        sa.Column("contact_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("governorate", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_customer_contacts_owner", "customer_contacts", ["owner_principal_id"])

    op.create_table(
        "customer_addresses",
        sa.Column("address_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("owner_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("governorate", sa.String(length=32), nullable=False),
        sa.Column("line", sa.String(length=512), nullable=False),
        sa.Column("contact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("landmark", sa.String(length=256), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        # A pin is meaningless with only one coordinate.
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name="ck_customer_addresses_geo_pair",
        ),
    )
    op.create_index(
        "ix_customer_addresses_owner_kind",
        "customer_addresses",
        ["owner_principal_id", "kind"],
    )
    # At most one live default per owner and kind: two would make "the pickup address"
    # ambiguous at the moment a courier is dispatched.
    op.create_index(
        "uq_customer_addresses_one_default",
        "customer_addresses",
        ["owner_principal_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default AND archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_customer_addresses_one_default", table_name="customer_addresses")
    op.drop_index("ix_customer_addresses_owner_kind", table_name="customer_addresses")
    op.drop_table("customer_addresses")
    op.drop_index("ix_customer_contacts_owner", table_name="customer_contacts")
    op.drop_table("customer_contacts")
    op.drop_table("customer_legal_acceptances")
    op.drop_table("customer_profiles")
