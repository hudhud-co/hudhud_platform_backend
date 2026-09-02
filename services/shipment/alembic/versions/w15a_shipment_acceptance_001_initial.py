"""Initial Shipment acceptance persistence tables.

Rollback: downgrade drops all tables. This Wave has no production data.
Forward recovery: re-run upgrade on a disposable database.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w15a_shipment_acceptance_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_intents",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
        sa.UniqueConstraint("shipment_id", name="uq_order_intents_shipment_id"),
    )

    op.create_table(
        "shipments",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("waybill_number", sa.String(length=128), nullable=False),
        sa.Column("current_status", sa.String(length=32), nullable=False),
        sa.Column("order_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_custody_type", sa.String(length=32), nullable=True),
        sa.Column("current_custody_id", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("shipment_id"),
        sa.UniqueConstraint("waybill_number", name="uq_shipments_waybill_number"),
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"], unique=False)

    op.create_table(
        "pickup_task_snapshots",
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_driver_user_id", sa.String(length=128), nullable=True),
        sa.Column("assigned_batch_id", sa.Uuid(), nullable=True),
        sa.Column("has_pickup_condition_proof", sa.Boolean(), nullable=False),
        sa.Column("acceptance_state", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("pickup_task_id"),
        sa.UniqueConstraint("shipment_id", name="uq_pickup_task_snapshots_shipment_id"),
    )
    op.create_index(
        "ix_pickup_task_snapshots_shipment_id",
        "pickup_task_snapshots",
        ["shipment_id"],
        unique=False,
    )

    op.create_table(
        "shipment_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "shipment_id",
            "event_type",
            name="uq_shipment_events_shipment_event_type",
        ),
    )
    op.create_index(
        "ix_shipment_events_shipment_id",
        "shipment_events",
        ["shipment_id"],
        unique=False,
    )

    op.create_table(
        "acceptance_audit_logs",
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(
        "ix_acceptance_audit_logs_entity",
        "acceptance_audit_logs",
        ["entity_type", "entity_id"],
        unique=False,
    )

    op.create_table(
        "acceptance_decisions",
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("acting_driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("scanned_identifier", sa.String(length=256), nullable=False),
        sa.Column("scan_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "exception_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint("shipment_id", name="uq_acceptance_decisions_shipment_id"),
        sa.UniqueConstraint("pickup_task_id", name="uq_acceptance_decisions_pickup_task_id"),
    )


def downgrade() -> None:
    op.drop_table("acceptance_decisions")
    op.drop_table("acceptance_audit_logs")
    op.drop_index("ix_shipment_events_shipment_id", table_name="shipment_events")
    op.drop_table("shipment_events")
    op.drop_index("ix_pickup_task_snapshots_shipment_id", table_name="pickup_task_snapshots")
    op.drop_table("pickup_task_snapshots")
    op.drop_index("ix_shipments_order_id", table_name="shipments")
    op.drop_table("shipments")
    op.drop_table("order_intents")
