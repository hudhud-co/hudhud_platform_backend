"""Convert bootstrap custody type DRIVER to PICKUP_DRIVER.

Evidence: `current_custody_type` is a free-form String(32); W15/W16 acceptance
writes used the bootstrap enum value DRIVER. ADR-0003 W17-A requires source-
aligned terminology PICKUP_DRIVER and an explicit data migration.

Upgrade converts existing DRIVER rows only. Domain/enum no longer accepts new
DRIVER writes (fail closed on read of unmigrated DRIVER).

Rollback: downgrade restores PICKUP_DRIVER → DRIVER for this boundary only.
Forward recovery: re-run upgrade on a disposable database.
Disposable PostgreSQL upgrade is deferred (no Docker/database in this Wave).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Keep ≤32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w17d_custody_pickup_driver_001"
down_revision: str | Sequence[str] | None = "w16a_acceptance_idempotency_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE shipments "
            "SET current_custody_type = 'PICKUP_DRIVER' "
            "WHERE current_custody_type = 'DRIVER'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE shipments "
            "SET current_custody_type = 'DRIVER' "
            "WHERE current_custody_type = 'PICKUP_DRIVER'"
        )
    )
