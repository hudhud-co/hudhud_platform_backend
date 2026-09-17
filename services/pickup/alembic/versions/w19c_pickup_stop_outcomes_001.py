"""W19-C: packaging decision, photo-documentation gate, and merchant-stop outcomes.

Expand only. Six additive columns on ``pickup_tasks`` plus one index. Every column is
nullable or carries a server default, so rows written before this migration keep their
exact meaning and no backfill is required.

* ``packaging_assessment`` / ``condition_decision`` record the driver's judgement at the
  door and what they decided to do about it. Existing rows leave them NULL; the
  application derives the assessment from ``package_condition_status`` in that case.
* ``photo_documentation_required`` defaults to ``false``, so the acceptance photo gate is
  inert until an owner sets it per shipment.
* ``stop_outcome`` / ``stop_outcome_reason`` / ``stop_outcome_at`` record a refused or
  not-presented parcel. Neither outcome starts custody, so no event and no Shipment
  change follows from them.

Rollback: downgrade drops the index and the six columns. Nothing else is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w19c_pickup_stop_outcomes_001"
down_revision: str | Sequence[str] | None = "w19a_pickup_driver_wave_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "pickup_tasks"
_INDEX = "ix_pickup_tasks_batch_outcome"

_NULLABLE_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("packaging_assessment", sa.String(length=32)),
    ("condition_decision", sa.String(length=32)),
    ("stop_outcome", sa.String(length=32)),
    ("stop_outcome_reason", sa.String(length=64)),
    ("stop_outcome_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    for name, column_type in _NULLABLE_COLUMNS:
        op.add_column(_TABLE, sa.Column(name, column_type, nullable=True))

    # Defaulted rather than nullable: the gate must read as "off" for every existing row,
    # never as "unknown".
    op.add_column(
        _TABLE,
        sa.Column(
            "photo_documentation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Stop readiness counts every expected parcel in one merchant stop.
    op.create_index(_INDEX, _TABLE, ["assigned_batch_id", "stop_outcome"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "photo_documentation_required")
    for name, _ in reversed(_NULLABLE_COLUMNS):
        op.drop_column(_TABLE, name)
