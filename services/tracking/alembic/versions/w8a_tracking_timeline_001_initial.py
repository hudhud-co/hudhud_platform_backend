"""Initial Tracking inbox and shipment timeline projection tables.

Display ordering uses (occurred_at, event_id) and is not commit ordering.
(occurred_at, source_pk/event_id) does not prove gap-free capture.
A1 does not create Tracking or Shipment authority.

Rollback: downgrade drops both tables. This Wave has no production data.
Forward recovery: re-run upgrade on a disposable database.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w8a_tracking_timeline_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracking_integration_inbox",
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_tracking_inbox_consumer_event",
        ),
        comment="ADR-0008 inbox for Tracking A1 consumer — not Shipment authority",
    )
    op.create_index(
        "ix_tracking_inbox_consumer_status",
        "tracking_integration_inbox",
        ["consumer_name", "status"],
        unique=False,
    )

    op.create_table(
        "shipment_timeline_entries",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_pk", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.String(length=256), nullable=False),
        sa.Column("source_module", sa.String(length=64), nullable=False),
        sa.Column("legacy_event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("bridge_mapper_version", sa.String(length=64), nullable=False),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "source_system",
            "source_table",
            "source_pk",
            name="uq_shipment_timeline_source_row",
        ),
        comment="Legacy A1 timeline projection — display ordering is not commit ordering",
    )
    op.create_index(
        "ix_shipment_timeline_shipment_occurred",
        "shipment_timeline_entries",
        ["shipment_id", "occurred_at", "event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shipment_timeline_shipment_occurred",
        table_name="shipment_timeline_entries",
    )
    op.drop_table("shipment_timeline_entries")
    op.drop_index("ix_tracking_inbox_consumer_status", table_name="tracking_integration_inbox")
    op.drop_table("tracking_integration_inbox")
