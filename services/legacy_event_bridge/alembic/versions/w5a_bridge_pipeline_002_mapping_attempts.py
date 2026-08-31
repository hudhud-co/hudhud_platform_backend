"""Add durable mapping attempt counter to bridge landing rows."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w5a_bridge_pipeline_002"
down_revision: str | Sequence[str] | None = "w5a_bridge_pipeline_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bridge_landing",
        sa.Column("mapping_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("bridge_landing", "mapping_attempt_count")
