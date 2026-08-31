"""Initial bridge landing, checkpoint, and outbox tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "w5a_bridge_pipeline_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bridge_landing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_table", sa.String(length=64), nullable=False),
        sa.Column("source_pk", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.String(length=256), nullable=False),
        sa.Column("mapper_version", sa.String(length=64), nullable=False),
        sa.Column("normalized_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mapping_state", sa.String(length=32), nullable=False),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system",
            "source_table",
            "source_pk",
            name="uq_bridge_landing_source_row",
        ),
    )
    op.create_index(
        "ix_bridge_landing_mapping_state",
        "bridge_landing",
        ["mapping_state"],
        unique=False,
    )

    op.create_table(
        "bridge_checkpoint",
        sa.Column("capture_source", sa.String(length=128), nullable=False),
        sa.Column("last_durably_landed_position", sa.String(length=256), nullable=True),
        sa.Column("last_feedback_eligible_position", sa.String(length=256), nullable=True),
        sa.Column(
            "last_external_slot_advanced_position",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("capture_source"),
    )

    op.create_table(
        "bridge_integration_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
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
        sa.Column("landing_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_bridge_outbox_event_id"),
    )
    op.create_index(
        "ix_bridge_outbox_pending",
        "bridge_integration_outbox",
        ["status", "next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bridge_outbox_pending",
        table_name="bridge_integration_outbox",
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.drop_table("bridge_integration_outbox")
    op.drop_table("bridge_checkpoint")
    op.drop_index("ix_bridge_landing_mapping_state", table_name="bridge_landing")
    op.drop_table("bridge_landing")
