"""W17-E: acceptance fields and Pickup transactional outbox.

Expand: additive columns on pickup_tasks; new outbox and acceptance idempotency tables.
Rollback: downgrade drops new tables and columns. No production data in this Wave.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w17e_pickup_accepted_outbox_001"
down_revision: str | Sequence[str] | None = "w15b_pickup_recovery_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pickup_tasks",
        sa.Column(
            "has_pickup_condition_proof",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "pickup_tasks",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pickup_tasks",
        sa.Column("accepted_by_driver_user_id", sa.String(length=128), nullable=True),
    )

    op.create_table(
        "pickup_acceptance_idempotency",
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
        comment="Acceptance command idempotency — one result and event_id per key",
    )

    op.create_table(
        "pickup_integration_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_pickup_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_pickup_outbox_aggregate_version",
        ),
        sa.UniqueConstraint(
            "event_type",
            "aggregate_id",
            name="uq_pickup_outbox_event_type_aggregate",
        ),
        comment="Pickup-owned transactional integration outbox",
    )
    op.create_index(
        "ix_pickup_outbox_pending",
        "pickup_integration_outbox",
        ["status", "next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pickup_outbox_pending",
        table_name="pickup_integration_outbox",
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.drop_table("pickup_integration_outbox")
    op.drop_table("pickup_acceptance_idempotency")
    op.drop_column("pickup_tasks", "accepted_by_driver_user_id")
    op.drop_column("pickup_tasks", "accepted_at")
    op.drop_column("pickup_tasks", "has_pickup_condition_proof")
