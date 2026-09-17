"""W19-B: canonical PICKUP_DRIVER → ORIGIN_HUB custody transfer.

Additive expand migration:

* ``shipments.custody_transferred_at`` records when custody left the pickup driver.
* ``shipment_custody_transfers`` is the canonical handover record. Its unique
  ``pickup_task_id`` is the at-least-once convergence key: a redelivered
  ``pickup.fact.handover_completed`` finds the existing row and converges instead of
  transferring custody twice.

No cross-service foreign keys — Pickup ids are referenced by value only.

Rollback: downgrade drops the table and the column. Forward recovery: re-run upgrade on
a disposable database. Disposable PostgreSQL upgrade is deferred (no Docker/database in
this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep ≤32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w19b_hub_custody_transfer_001"
down_revision: str | Sequence[str] | None = "w17f_accepted_inbox_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column("custody_transferred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "shipment_custody_transfers",
        sa.Column("transfer_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("handover_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("from_custody_type", sa.String(length=32), nullable=False),
        sa.Column("from_custody_id", sa.String(length=128), nullable=False),
        sa.Column("to_custody_type", sa.String(length=32), nullable=False),
        sa.Column("to_custody_id", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("discrepancy_reason", sa.String(length=32), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receiving_actor_id", sa.String(length=128), nullable=False),
        sa.Column(
            "condition_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("transfer_id"),
        sa.UniqueConstraint(
            "pickup_task_id", name="uq_shipment_custody_transfers_pickup_task_id"
        ),
        sa.CheckConstraint(
            "from_custody_id <> receiving_actor_id",
            name="ck_shipment_custody_transfers_actor_differs",
        ),
        sa.CheckConstraint(
            "outcome IN ('RECEIVED', 'RECEIVED_WITH_DISCREPANCY')",
            name="ck_shipment_custody_transfers_releasing_outcome",
        ),
    )
    op.create_index(
        "ix_shipment_custody_transfers_shipment_id",
        "shipment_custody_transfers",
        ["shipment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shipment_custody_transfers_shipment_id",
        table_name="shipment_custody_transfers",
    )
    op.drop_table("shipment_custody_transfers")
    op.drop_column("shipments", "custody_transferred_at")
