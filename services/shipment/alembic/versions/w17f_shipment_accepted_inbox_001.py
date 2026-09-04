"""Add Shipment-owned ADR-0008 integration inbox for pickup.fact.accepted.

Additive expand migration. Unique (consumer_name, event_id) is the inbox
idempotency gate. No cross-service foreign keys.

Rollback: downgrade drops the inbox table. Safe while no production consumer
traffic. Forward recovery: re-run upgrade on a disposable database.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Keep ≤32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w17f_accepted_inbox_001"
down_revision: str | Sequence[str] | None = "w17d_custody_pickup_driver_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipment_integration_inbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("processing_owner", sa.String(length=128), nullable=True),
        sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handler_version", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("jetstream_stream", sa.String(length=128), nullable=True),
        sa.Column("jetstream_seq", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("nats_msg_id", sa.String(length=128), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=True),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("aggregate_version", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_shipment_inbox_consumer_event",
        ),
        comment="ADR-0008 inbox for Shipment pickup.fact.accepted consumer",
    )
    op.create_index(
        "ix_shipment_inbox_consumer_status",
        "shipment_integration_inbox",
        ["consumer_name", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shipment_inbox_consumer_status",
        table_name="shipment_integration_inbox",
    )
    op.drop_table("shipment_integration_inbox")
