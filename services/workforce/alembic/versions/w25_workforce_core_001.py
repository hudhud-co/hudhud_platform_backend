"""W25: Workforce core — applications, drivers, shift patterns, attendance, blocks, leave.

Expand-only: this migration creates new tables and touches nothing that exists.

The rules carried into the database are the ones that decide whether someone can be given
work, and each of them is one concurrent request away from being broken:

* a verified application always names the operator and office that checked it (SEC-03);
* one open application per applicant, and one open lateness block per driver;
* a cleared block always names who cleared it (OPS-09);
* a declined leave request always says why;
* hourly leave states its hours and sits inside one day.

There is no outbox table: Workforce answers "is this driver assignable?" over HTTP.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w25_workforce_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "driver_applications",
        sa.Column("application_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("applicant_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=32), nullable=False),
        sa.Column("vehicle_plate_number", sa.String(length=16), nullable=False),
        sa.Column("vehicle_model", sa.String(length=64), nullable=True),
        sa.Column(
            "declared_shifts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("terms_version_accepted", sa.String(length=32), nullable=True),
        sa.Column(
            "documents_received",
            postgresql.ARRAY(sa.String(length=32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "documents_verified",
            postgresql.ARRAY(sa.String(length=32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("verified_at_office", sa.String(length=160), nullable=True),
        sa.Column("decision_reason", sa.String(length=512), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("reference", name="uq_driver_application_reference"),
        sa.CheckConstraint(
            "status <> 'VERIFIED' OR ("
            "verified_by_actor_id IS NOT NULL AND verified_at_office IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="ck_driver_application_verification_is_attributed",
        ),
    )
    op.create_index(
        "uq_driver_application_one_open",
        "driver_applications",
        ["applicant_principal_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('SUBMITTED', 'AWAITING_OFFICE_VERIFICATION')"
        ),
    )
    op.create_index("ix_driver_application_status", "driver_applications", ["status"])

    op.create_table(
        "drivers",
        sa.Column("driver_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("vehicle_kind", sa.String(length=32), nullable=False),
        sa.Column("vehicle_plate_number", sa.String(length=16), nullable=False),
        sa.Column("vehicle_model", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("principal_id", name="uq_driver_principal"),
        sa.UniqueConstraint("application_id", name="uq_driver_application"),
    )
    op.create_index("ix_driver_status", "drivers", ["status"])

    op.create_table(
        "driver_shift_patterns",
        sa.Column("pattern_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "windows",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "jsonb_array_length(windows) >= 1", name="ck_shift_pattern_has_a_window"
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_shift_pattern_window_ordered",
        ),
    )
    op.create_index(
        "ix_shift_pattern_driver",
        "driver_shift_patterns",
        ["driver_id", "effective_from"],
    )

    op.create_table(
        "driver_attendance",
        sa.Column("attendance_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("scheduled_start", sa.Time(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lateness_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("driver_id", "shift_date", name="uq_attendance_driver_day"),
        sa.CheckConstraint(
            "lateness_minutes >= 0", name="ck_attendance_lateness_not_negative"
        ),
        sa.CheckConstraint(
            "status <> 'STARTED' OR started_at IS NOT NULL",
            name="ck_attendance_started_has_time",
        ),
    )
    op.create_index(
        "ix_attendance_driver", "driver_attendance", ["driver_id", "shift_date"]
    )

    op.create_table(
        "driver_lateness_blocks",
        sa.Column("block_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("scheduled_start", sa.Time(), nullable=False),
        sa.Column("opened_app_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delay_minutes", sa.Integer(), nullable=False),
        sa.Column("blocked_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unblock_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("clearing_note", sa.String(length=512), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "(cleared_at IS NULL) = (cleared_by_actor_id IS NULL)",
            name="ck_lateness_block_clearing_is_attributed",
        ),
        sa.CheckConstraint(
            "delay_minutes >= 0", name="ck_lateness_block_delay_not_negative"
        ),
    )
    op.create_index(
        "uq_lateness_block_one_active",
        "driver_lateness_blocks",
        ["driver_id"],
        unique=True,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )

    op.create_table(
        "driver_leave_requests",
        sa.Column("leave_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=True),
        sa.Column("ends_at", sa.Time(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=1024), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.String(length=1024), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("reference", name="uq_leave_reference"),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_window_ordered"),
        sa.CheckConstraint(
            "(kind = 'HOURLY') = (starts_at IS NOT NULL AND ends_at IS NOT NULL)",
            name="ck_leave_hours_match_kind",
        ),
        sa.CheckConstraint(
            "kind <> 'HOURLY' OR start_date = end_date",
            name="ck_leave_hourly_is_one_day",
        ),
        sa.CheckConstraint(
            "status <> 'DECLINED' OR decision_note IS NOT NULL",
            name="ck_leave_decline_has_reason",
        ),
    )
    op.create_index("ix_leave_driver", "driver_leave_requests", ["driver_id", "start_date"])
    op.create_index(
        "ix_leave_pending",
        "driver_leave_requests",
        ["status"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "workforce_integration_inbox",
        sa.Column("inbox_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "consumer_name", "event_id", name="uq_workforce_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_workforce_inbox_status",
        "workforce_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_table("workforce_integration_inbox")
    op.drop_table("driver_leave_requests")
    op.drop_table("driver_lateness_blocks")
    op.drop_table("driver_attendance")
    op.drop_table("driver_shift_patterns")
    op.drop_table("drivers")
    op.drop_table("driver_applications")
