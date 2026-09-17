"""W23: Hub core — hubs, drop-offs, parcel presence, consignments, linehaul, messaging.

Expand-only: this migration creates new tables and touches nothing that exists.

The rules it carries into the database are the ones a concurrent counter could otherwise
break: one live drop-off per tracking code, one live label, an accepted drop-off that
always names its label, weight and operator, a held parcel that always says why, and a
sealed consignment that always records who sealed it.

The per-hub cut-off (v6.3 p.23) is a column on `hubs`, not a constant anywhere in code.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w23_hub_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hubs",
        sa.Column("hub_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("governorate", sa.String(length=32), nullable=False),
        sa.Column("cut_off_local_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "vehicle_cameras_fitted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("code", name="uq_hub_code"),
    )

    op.create_table(
        "hub_drop_offs",
        sa.Column("drop_off_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("shipment_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sender_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "captured_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("label_code", sa.String(length=32), nullable=True),
        sa.Column("labelled_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status <> 'ACCEPTED' OR ("
            "label_code IS NOT NULL AND weight_grams IS NOT NULL "
            "AND accepted_by_actor_id IS NOT NULL)",
            name="ck_hub_drop_off_accepted_is_complete",
        ),
        sa.CheckConstraint(
            "weight_grams IS NULL OR weight_grams > 0",
            name="ck_hub_drop_off_weight_positive",
        ),
    )
    op.create_index(
        "uq_hub_drop_off_live_tracking_code",
        "hub_drop_offs",
        ["tracking_code"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('EXPECTED', 'DETAILS_CAPTURED', 'LABELLED')"
        ),
    )
    op.create_index(
        "uq_hub_drop_off_label_code",
        "hub_drop_offs",
        ["label_code"],
        unique=True,
        postgresql_where=sa.text("label_code IS NOT NULL AND status <> 'CANCELLED'"),
    )
    op.create_index("ix_hub_drop_off_hub_status", "hub_drop_offs", ["hub_id", "status"])

    op.create_table(
        "hub_parcel_presences",
        sa.Column("presence_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("destination_governorate", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("routing_decision", sa.String(length=32), nullable=True),
        sa.Column("route_code", sa.String(length=32), nullable=True),
        sa.Column("consignment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sorted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_to_last_mile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_reason", sa.String(length=40), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("hub_id", "tracking_code", name="uq_hub_presence_parcel"),
        sa.CheckConstraint(
            "status <> 'HELD' OR hold_reason IS NOT NULL",
            name="ck_hub_presence_held_has_reason",
        ),
    )
    op.create_index(
        "ix_hub_presence_hub_status", "hub_parcel_presences", ["hub_id", "status"]
    )
    op.create_index(
        "ix_hub_presence_consignment", "hub_parcel_presences", ["consignment_id"]
    )

    op.create_table(
        "hub_consignments",
        sa.Column("consignment_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("origin_hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("destination_hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("seal_code", sa.String(length=32), nullable=True),
        sa.Column("seal_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seal_applied_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "parcel_codes",
            postgresql.ARRAY(sa.String(length=24)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "(seal_code IS NULL) = (seal_applied_at IS NULL)",
            name="ck_hub_consignment_seal_pair",
        ),
        sa.CheckConstraint(
            "seal_code IS NULL OR seal_applied_by_actor_id IS NOT NULL",
            name="ck_hub_consignment_seal_attributed",
        ),
        sa.CheckConstraint(
            "origin_hub_id <> destination_hub_id",
            name="ck_hub_consignment_between_two_hubs",
        ),
    )
    op.create_index(
        "uq_hub_consignment_seal_code",
        "hub_consignments",
        ["seal_code"],
        unique=True,
        postgresql_where=sa.text("seal_code IS NOT NULL"),
    )
    op.create_index(
        "ix_hub_consignment_origin", "hub_consignments", ["origin_hub_id", "status"]
    )
    op.create_index(
        "ix_hub_consignment_destination",
        "hub_consignments",
        ["destination_hub_id", "status"],
    )

    op.create_table(
        "hub_seal_checks",
        sa.Column("check_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consignment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("expected_seal_code", sa.String(length=32), nullable=False),
        sa.Column("observed_seal_code", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_by_actor_id", sa.Uuid(as_uuid=True), nullable=False),
    )
    op.create_index(
        "ix_hub_seal_check_consignment", "hub_seal_checks", ["consignment_id"]
    )

    op.create_table(
        "hub_linehauls",
        sa.Column("linehaul_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consignment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("origin_hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("destination_hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("vehicle_reference", sa.String(length=64), nullable=False),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("planned_departure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "route_deviation_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("deviation_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("consignment_id", name="uq_hub_linehaul_consignment"),
        sa.CheckConstraint(
            "origin_hub_id <> destination_hub_id",
            name="ck_hub_linehaul_between_two_hubs",
        ),
    )
    op.create_index("ix_hub_linehaul_origin", "hub_linehauls", ["origin_hub_id", "status"])

    op.create_table(
        "hub_vehicle_positions",
        sa.Column("position_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("linehaul_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_hub_position_linehaul", "hub_vehicle_positions", ["linehaul_id", "recorded_at"]
    )

    op.create_table(
        "hub_integration_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column(
            "payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
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
        sa.UniqueConstraint("event_id", name="uq_hub_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id", "aggregate_version", name="uq_hub_outbox_aggregate_version"
        ),
    )
    op.create_index(
        "ix_hub_outbox_pending",
        "hub_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "hub_integration_inbox",
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
            "consumer_name", "event_id", name="uq_hub_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_hub_inbox_status",
        "hub_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_table("hub_integration_inbox")
    op.drop_table("hub_integration_outbox")
    op.drop_table("hub_vehicle_positions")
    op.drop_table("hub_linehauls")
    op.drop_table("hub_seal_checks")
    op.drop_table("hub_consignments")
    op.drop_table("hub_parcel_presences")
    op.drop_table("hub_drop_offs")
    op.drop_table("hubs")
