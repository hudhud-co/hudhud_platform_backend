"""Initial Pickup recovery persistence tables.

Rollback: downgrade drops all three tables. This Wave has no production data.
Forward recovery: re-run upgrade on a disposable database.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w15b_pickup_recovery_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pickup_tasks",
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("assigned_batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("root_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("parent_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_task_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acceptance_state", sa.String(length=32), nullable=True),
        sa.Column("recovery_reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("pickup_task_id"),
        sa.UniqueConstraint(
            "root_attempt_id",
            "attempt_number",
            name="uq_pickup_tasks_root_attempt_number",
        ),
        comment="Pickup-owned task attempts with explicit lineage",
    )
    op.create_index(
        "ix_pickup_tasks_shipment_id",
        "pickup_tasks",
        ["shipment_id"],
        unique=False,
    )
    op.create_index(
        "ix_pickup_tasks_root_attempt_id",
        "pickup_tasks",
        ["root_attempt_id"],
        unique=False,
    )

    op.create_table(
        "pickup_recovery_history",
        sa.Column("history_id", sa.Uuid(), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("replacement_task_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("history_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_pickup_recovery_history_idempotency_key",
        ),
        comment="Append-only Pickup recovery audit trail",
    )
    op.create_index(
        "ix_pickup_recovery_history_pickup_task_id",
        "pickup_recovery_history",
        ["pickup_task_id"],
        unique=False,
    )

    op.create_table(
        "pickup_recovery_idempotency",
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("original_task_id", sa.Uuid(), nullable=False),
        sa.Column("result_task_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
        comment="Recovery command idempotency outcomes — one result per key",
    )


def downgrade() -> None:
    op.drop_table("pickup_recovery_idempotency")
    op.drop_index("ix_pickup_recovery_history_pickup_task_id", table_name="pickup_recovery_history")
    op.drop_table("pickup_recovery_history")
    op.drop_index("ix_pickup_tasks_root_attempt_id", table_name="pickup_tasks")
    op.drop_index("ix_pickup_tasks_shipment_id", table_name="pickup_tasks")
    op.drop_table("pickup_tasks")
