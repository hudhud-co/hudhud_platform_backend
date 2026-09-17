"""W19-A: driver workforce, sender handover ceremony, hub handover, offline queue.

Expand: additive columns on ``pickup_tasks`` plus nine new Pickup-owned tables for the
driver work session, courier challenge/manifest, hub handover manifest and its items,
offline authorization/stream/event queue, reconciliation cases, and audit history.

Rollback: downgrade drops every new table and column. No cross-service foreign keys are
created — related aggregates are referenced by id only.

Concurrency-critical constraints created here:

* one open work session per driver (partial unique index)
* one open courier challenge and one open courier manifest per pickup task
* one open handover manifest line per shipment
* one offline capture per ``(driver_user_id, operation_id)`` and per
  ``(stream_id, sequence)``
* one open reconciliation case per offline event
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w19a_pickup_driver_wave_001"
down_revision: str | Sequence[str] | None = "w17e_pickup_accepted_outbox_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("declined_at", sa.DateTime(timezone=True)),
    ("arrived_at", sa.DateTime(timezone=True)),
    ("scanned_at", sa.DateTime(timezone=True)),
    ("condition_proof_captured_at", sa.DateTime(timezone=True)),
    ("exception_reported_at", sa.DateTime(timezone=True)),
    ("failed_at", sa.DateTime(timezone=True)),
)

_NEW_TABLES: tuple[str, ...] = (
    "pickup_offline_reconciliation_cases",
    "pickup_offline_events",
    "pickup_offline_streams",
    "pickup_offline_authorizations",
    "pickup_handover_manifest_items",
    "pickup_handover_manifests",
    "pickup_courier_manifests",
    "pickup_courier_challenges",
    "pickup_driver_work_sessions",
    "pickup_task_history",
)


def upgrade() -> None:
    _upgrade_pickup_tasks()
    _create_task_history()
    _create_work_sessions()
    _create_courier_ceremony()
    _create_hub_handover()
    _create_offline_queue()


def _upgrade_pickup_tasks() -> None:
    op.add_column(
        "pickup_tasks",
        sa.Column(
            "assignment_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'ACKNOWLEDGED'"),
        ),
    )
    # New rows default to OFFERED; existing rows are backfilled as ACKNOWLEDGED above
    # because they were created before the offer/decline step existed.
    op.alter_column(
        "pickup_tasks",
        "assignment_state",
        server_default=sa.text("'OFFERED'"),
    )
    op.add_column(
        "pickup_tasks", sa.Column("declined_reason", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "pickup_tasks", sa.Column("scanned_identifier", sa.String(length=256), nullable=True)
    )
    op.add_column(
        "pickup_tasks",
        sa.Column("package_condition_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pickup_tasks", sa.Column("exception_reason", sa.String(length=64), nullable=True)
    )
    for name, column_type in _TASK_COLUMNS:
        op.add_column("pickup_tasks", sa.Column(name, column_type, nullable=True))
    # The driver workload query runs on every work-session command.
    op.create_index(
        "ix_pickup_tasks_driver_status",
        "pickup_tasks",
        ["assigned_driver_user_id", "status"],
    )


def _create_task_history() -> None:
    op.create_table(
        "pickup_task_history",
        sa.Column("history_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("history_id"),
    )
    op.create_index(
        "ix_pickup_task_history_task",
        "pickup_task_history",
        ["pickup_task_id", "occurred_at"],
    )
    op.create_index("ix_pickup_task_history_actor", "pickup_task_history", ["actor_id"])


def _create_work_sessions() -> None:
    op.create_table(
        "pickup_driver_work_sessions",
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_hub_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(length=32), nullable=True),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.Column(
            "session_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "uq_pickup_work_session_open_driver",
        "pickup_driver_work_sessions",
        ["driver_user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE', 'PAUSED')"),
    )
    op.create_index(
        "ix_pickup_work_session_driver",
        "pickup_driver_work_sessions",
        ["driver_user_id", "started_at"],
    )


def _create_courier_ceremony() -> None:
    op.create_table(
        "pickup_courier_challenges",
        sa.Column("challenge_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("shipment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assigned_driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("sender_type", sa.String(length=32), nullable=False),
        sa.Column("assignment_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("issued_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "failed_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifier_user_id", sa.String(length=128), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("challenge_id"),
    )
    op.create_index(
        "ix_pickup_courier_challenge_task",
        "pickup_courier_challenges",
        ["pickup_task_id", "status"],
    )
    op.create_index(
        "uq_pickup_courier_challenge_open_task",
        "pickup_courier_challenges",
        ["pickup_task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ISSUED', 'VERIFIED')"),
    )

    op.create_table(
        "pickup_courier_manifests",
        sa.Column("manifest_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("shipment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("manifest_id"),
    )
    op.create_index(
        "ix_pickup_courier_manifest_task",
        "pickup_courier_manifests",
        ["pickup_task_id", "status"],
    )
    op.create_index(
        "uq_pickup_courier_manifest_open_task",
        "pickup_courier_manifests",
        ["pickup_task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('SUBMITTED', 'CONFIRMED')"),
    )


def _create_hub_handover() -> None:
    op.create_table(
        "pickup_handover_manifests",
        sa.Column("manifest_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("manifest_code", sa.String(length=32), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("received_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "discrepancy_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at_hub_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.Column(
            "manifest_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("manifest_id"),
        sa.UniqueConstraint("manifest_code", name="uq_pickup_handover_manifest_code"),
        sa.CheckConstraint("expected_count >= 0", name="ck_pickup_handover_expected_count"),
    )
    op.create_index(
        "ix_pickup_handover_manifest_driver",
        "pickup_handover_manifests",
        ["driver_user_id", "status"],
    )

    op.create_table(
        "pickup_handover_manifest_items",
        sa.Column("item_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("manifest_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("pickup_task_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("shipment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("received_hub_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("discrepancy_reason", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("item_id"),
        sa.UniqueConstraint(
            "manifest_id",
            "shipment_id",
            name="uq_pickup_handover_item_manifest_shipment",
        ),
    )
    op.create_index(
        "uq_pickup_handover_item_open_shipment",
        "pickup_handover_manifest_items",
        ["shipment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('EXPECTED')"),
    )
    op.create_index(
        "ix_pickup_handover_item_shipment",
        "pickup_handover_manifest_items",
        ["shipment_id", "status"],
    )


def _create_offline_queue() -> None:
    op.create_table(
        "pickup_offline_authorizations",
        sa.Column("authorization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("device_id_hash", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assignment_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "permitted_operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "resource_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("authorization_id"),
        sa.UniqueConstraint("token_hash", name="uq_pickup_offline_authorization_token"),
    )
    op.create_index(
        "ix_pickup_offline_authorization_driver",
        "pickup_offline_authorizations",
        ["driver_user_id", "issued_at"],
    )
    op.create_index(
        "ix_pickup_offline_authorization_resource",
        "pickup_offline_authorizations",
        ["resource_id"],
    )

    op.create_table(
        "pickup_offline_streams",
        sa.Column("stream_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("authorization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("device_id_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "last_contiguous_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("stream_id"),
    )
    op.create_index(
        "ix_pickup_offline_stream_driver", "pickup_offline_streams", ["driver_user_id"]
    )

    op.create_table(
        "pickup_offline_events",
        sa.Column("event_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stream_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("authorization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assignment_revision", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome_code", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replayed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("event_row_id"),
        sa.UniqueConstraint(
            "driver_user_id",
            "operation_id",
            name="uq_pickup_offline_event_driver_operation",
        ),
        sa.UniqueConstraint(
            "stream_id", "sequence", name="uq_pickup_offline_event_stream_sequence"
        ),
    )
    op.create_index(
        "ix_pickup_offline_event_driver_status",
        "pickup_offline_events",
        ["driver_user_id", "status"],
    )
    op.create_index(
        "ix_pickup_offline_event_resource", "pickup_offline_events", ["resource_id"]
    )

    op.create_table(
        "pickup_offline_reconciliation_cases",
        sa.Column("case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("offline_event_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("driver_user_id", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column(
            "authoritative_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "submitted_event",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "custody_implication", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index(
        "uq_pickup_reconciliation_open_event",
        "pickup_offline_reconciliation_cases",
        ["offline_event_row_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index(
        "ix_pickup_reconciliation_driver_status",
        "pickup_offline_reconciliation_cases",
        ["driver_user_id", "status"],
    )
    op.create_index(
        "ix_pickup_reconciliation_custody",
        "pickup_offline_reconciliation_cases",
        ["custody_implication", "status"],
    )


def downgrade() -> None:
    for table in _NEW_TABLES:
        op.drop_table(table)
    op.drop_index("ix_pickup_tasks_driver_status", table_name="pickup_tasks")
    for name, _ in _TASK_COLUMNS:
        op.drop_column("pickup_tasks", name)
    op.drop_column("pickup_tasks", "exception_reason")
    op.drop_column("pickup_tasks", "package_condition_status")
    op.drop_column("pickup_tasks", "scanned_identifier")
    op.drop_column("pickup_tasks", "declined_reason")
    op.drop_column("pickup_tasks", "assignment_state")
