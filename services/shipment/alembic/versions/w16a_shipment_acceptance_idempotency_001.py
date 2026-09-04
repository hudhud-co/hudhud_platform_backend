"""Add acceptance command idempotency table.

Rollback: downgrade drops acceptance_idempotency only.
Forward recovery: re-run upgrade on a disposable database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w16a_shipment_acceptance_idempotency_001"
down_revision: str | Sequence[str] | None = "w15a_shipment_acceptance_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "acceptance_idempotency",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("acceptance_idempotency")
