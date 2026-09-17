"""W26: Delivery core — manifests, stops, verification, payment, outcomes, ratings.

Expand-only: this migration creates new tables and touches nothing that exists.

Two things this schema deliberately does **not** contain, and both are business
decisions rather than oversights:

* no column holds a delivery code. ``delivery_stops.delivery_code_digest`` is a keyed
  HMAC-SHA256 digest bound to the stop, and the constraint below refuses anything that
  is not 64 hex characters long (DRV-L05);
* no column holds an identity-document photograph. Until the retention period is
  decided there is nowhere to put one, so nothing has to be deleted later (DRV-L07).

The rules carried into the database are the ones a concurrent request could otherwise
break: a delivered stop is always a verified stop, a closed stop always has a closing
time, a POS payment always carries proof, and an operations decision always names who
made it.

Money is integer minor units with an explicit currency. There is no NUMERIC and no
float on any monetary column; the only NUMERIC here is a map coordinate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w26_delivery_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_manifests",
        sa.Column("manifest_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    # v6.3 p.25 — a driver carries one round at a time.
    op.create_index(
        "uq_delivery_manifest_one_open_per_driver",
        "delivery_manifests",
        ["driver_principal_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index("ix_delivery_manifest_hub", "delivery_manifests", ["hub_id"])

    op.create_table(
        "delivery_stops",
        sa.Column("stop_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("manifest_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("driver_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "open_box_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "photo_documentation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("packaging_seal_code", sa.String(length=64), nullable=True),
        sa.Column("delivery_code_digest", sa.String(length=64), nullable=True),
        sa.Column("named_receiver", sa.String(length=160), nullable=True),
        sa.Column("cod_amount_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("cod_amount_currency", sa.String(length=3), nullable=True),
        sa.Column("payment_method_expected", sa.String(length=32), nullable=False),
        sa.Column("custody_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wait_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_method", sa.String(length=32), nullable=True),
        sa.Column("seal_outcome", sa.String(length=32), nullable=True),
        sa.Column("inspection_outcome", sa.String(length=32), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=32), nullable=True),
        sa.Column("refusal_reason", sa.String(length=32), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "code_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(
            "status <> 'DELIVERED' OR ("
            "verified_at IS NOT NULL AND verified_by_method IS NOT NULL "
            "AND delivered_at IS NOT NULL)",
            name="ck_delivery_stop_delivered_is_verified",
        ),
        sa.CheckConstraint(
            "(status IN ('DELIVERED', 'REFUSED', 'FAILED')) = (closed_at IS NOT NULL)",
            name="ck_delivery_stop_closed_at_matches_status",
        ),
        sa.CheckConstraint(
            "status = 'DELIVERED' OR delivered_at IS NULL",
            name="ck_delivery_stop_only_delivered_has_delivered_at",
        ),
        sa.CheckConstraint(
            "delivery_code_digest IS NULL OR char_length(delivery_code_digest) = 64",
            name="ck_delivery_stop_code_digest_is_a_digest",
        ),
        sa.CheckConstraint(
            "cod_amount_minor_units IS NULL OR cod_amount_minor_units >= 0",
            name="ck_delivery_stop_cod_amount_not_negative",
        ),
        sa.CheckConstraint(
            "(cod_amount_minor_units IS NULL) = (cod_amount_currency IS NULL)",
            name="ck_delivery_stop_cod_amount_has_currency",
        ),
        sa.CheckConstraint(
            "code_attempt_count >= 0",
            name="ck_delivery_stop_attempts_not_negative",
        ),
    )
    # One live stop per parcel. A retry after a failed attempt is a new stop, so the
    # index is partial rather than a plain unique constraint.
    op.create_index(
        "uq_delivery_stop_one_live_per_parcel",
        "delivery_stops",
        ["tracking_code"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('DELIVERED', 'REFUSED', 'FAILED')"),
    )
    op.create_index(
        "ix_delivery_stop_manifest", "delivery_stops", ["manifest_id", "created_at"]
    )
    op.create_index(
        "ix_delivery_stop_driver_open",
        "delivery_stops",
        ["driver_principal_id", "status"],
    )

    op.create_table(
        "delivery_verification_attempts",
        sa.Column("attempt_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("stop_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_by_actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("id_matched_named_receiver", sa.Boolean(), nullable=True),
    )
    op.create_index(
        "ix_delivery_verification_stop",
        "delivery_verification_attempts",
        ["stop_id", "attempted_at"],
    )

    op.create_table(
        "delivery_payments",
        sa.Column("payment_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("stop_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("amount_currency", sa.String(length=3), nullable=True),
        sa.Column("pos_reference", sa.String(length=64), nullable=True),
        sa.Column("pos_receipt_bucket", sa.String(length=128), nullable=True),
        sa.Column("pos_receipt_key", sa.String(length=512), nullable=True),
        sa.Column("pos_receipt_content_type", sa.String(length=128), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("stop_id", name="uq_delivery_payment_one_per_stop"),
        sa.CheckConstraint(
            "amount_minor_units IS NULL OR amount_minor_units >= 0",
            name="ck_delivery_payment_amount_not_negative",
        ),
        sa.CheckConstraint(
            "(amount_minor_units IS NULL) = (amount_currency IS NULL)",
            name="ck_delivery_payment_amount_has_currency",
        ),
        # Driver App v8 `lmPayApproved` — "PROOF OF PAYMENT — ONE IS REQUIRED".
        sa.CheckConstraint(
            "method <> 'POS_CARD' OR outcome <> 'COLLECTED' OR ("
            "pos_reference IS NOT NULL OR pos_receipt_key IS NOT NULL)",
            name="ck_delivery_payment_pos_has_proof",
        ),
        sa.CheckConstraint(
            "outcome <> 'COLLECTED' OR amount_minor_units IS NOT NULL",
            name="ck_delivery_payment_collected_has_amount",
        ),
    )

    op.create_table(
        "delivery_photo_evidence",
        sa.Column("photo_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("stop_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("media_bucket", sa.String(length=128), nullable=False),
        sa.Column("media_key", sa.String(length=512), nullable=False),
        sa.Column("media_content_type", sa.String(length=128), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_by_actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.UniqueConstraint("stop_id", "stage", name="uq_delivery_photo_stop_stage"),
    )

    op.create_table(
        "delivery_failed_attempts",
        sa.Column("attempt_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("stop_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("next_attempt_decision", sa.String(length=32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("stop_id", name="uq_delivery_failed_attempt_one_per_stop"),
        # OPS-08 — a disposition nobody made is not an operations decision.
        sa.CheckConstraint(
            "(next_attempt_decision IS NULL) = (decided_at IS NULL)",
            name="ck_delivery_failed_attempt_decision_has_time",
        ),
        sa.CheckConstraint(
            "next_attempt_decision IS NULL OR decided_by_actor_id IS NOT NULL",
            name="ck_delivery_failed_attempt_decision_is_attributed",
        ),
    )
    op.create_index(
        "ix_delivery_failed_attempt_awaiting_operations",
        "delivery_failed_attempts",
        ["recorded_at"],
        postgresql_where=sa.text("next_attempt_decision IS NULL"),
    )

    op.create_table(
        "delivery_receiver_preferences",
        sa.Column("preference_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("window_starts_at_hour", sa.Integer(), nullable=True),
        sa.Column("window_ends_at_hour", sa.Integer(), nullable=True),
        sa.Column("address_line", sa.String(length=256), nullable=True),
        sa.Column("landmark", sa.String(length=256), nullable=True),
        # A map coordinate, not money.
        sa.Column("geo_latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("geo_longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("set_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint(
            "tracking_code", name="uq_delivery_receiver_preference_tracking_code"
        ),
        sa.CheckConstraint(
            "(window_starts_at_hour IS NULL) = (window_ends_at_hour IS NULL)",
            name="ck_delivery_receiver_preference_window_is_complete",
        ),
        sa.CheckConstraint(
            "window_starts_at_hour IS NULL OR ("
            "window_starts_at_hour BETWEEN 0 AND 23 "
            "AND window_ends_at_hour BETWEEN 1 AND 24 "
            "AND window_ends_at_hour > window_starts_at_hour)",
            name="ck_delivery_receiver_preference_window_lies_within_a_day",
        ),
        sa.CheckConstraint(
            "(geo_latitude IS NULL) = (geo_longitude IS NULL)",
            name="ck_delivery_receiver_preference_geo_is_a_pair",
        ),
    )

    op.create_table(
        "delivery_issue_reports",
        sa.Column("report_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("stop_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("reported_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "media_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # DRV-L21 — a driver's incident is raised from a stop and names it.
        sa.CheckConstraint(
            "source <> 'DRIVER' OR stop_id IS NOT NULL",
            name="ck_delivery_issue_driver_report_names_a_stop",
        ),
    )
    op.create_index(
        "ix_delivery_issue_source",
        "delivery_issue_reports",
        ["source", "reported_at"],
    )
    op.create_index(
        "ix_delivery_issue_tracking_code",
        "delivery_issue_reports",
        ["tracking_code", "reported_at"],
    )

    op.create_table(
        "delivery_courier_ratings",
        sa.Column("rating_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("courier_principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # SEC-08 — the note and the rater never leave as anything but an aggregate.
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rated_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("tracking_code", name="uq_delivery_rating_one_per_parcel"),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_delivery_rating_score_range"),
    )
    op.create_index(
        "ix_delivery_rating_courier",
        "delivery_courier_ratings",
        ["courier_principal_id"],
    )

    op.create_table(
        "delivery_integration_outbox",
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
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_owner", sa.String(length=128), nullable=True),
        sa.Column("processing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_delivery_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_delivery_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_delivery_outbox_pending",
        "delivery_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "delivery_integration_inbox",
        sa.Column("inbox_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
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
            "consumer_name", "event_id", name="uq_delivery_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_delivery_inbox_status",
        "delivery_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_inbox_status", table_name="delivery_integration_inbox")
    op.drop_table("delivery_integration_inbox")
    op.drop_index("ix_delivery_outbox_pending", table_name="delivery_integration_outbox")
    op.drop_table("delivery_integration_outbox")
    op.drop_index("ix_delivery_rating_courier", table_name="delivery_courier_ratings")
    op.drop_table("delivery_courier_ratings")
    op.drop_index(
        "ix_delivery_issue_tracking_code", table_name="delivery_issue_reports"
    )
    op.drop_index("ix_delivery_issue_source", table_name="delivery_issue_reports")
    op.drop_table("delivery_issue_reports")
    op.drop_table("delivery_receiver_preferences")
    op.drop_index(
        "ix_delivery_failed_attempt_awaiting_operations",
        table_name="delivery_failed_attempts",
    )
    op.drop_table("delivery_failed_attempts")
    op.drop_table("delivery_photo_evidence")
    op.drop_table("delivery_payments")
    op.drop_index(
        "ix_delivery_verification_stop", table_name="delivery_verification_attempts"
    )
    op.drop_table("delivery_verification_attempts")
    op.drop_index("ix_delivery_stop_driver_open", table_name="delivery_stops")
    op.drop_index("ix_delivery_stop_manifest", table_name="delivery_stops")
    op.drop_index("uq_delivery_stop_one_live_per_parcel", table_name="delivery_stops")
    op.drop_table("delivery_stops")
    op.drop_index("ix_delivery_manifest_hub", table_name="delivery_manifests")
    op.drop_index(
        "uq_delivery_manifest_one_open_per_driver", table_name="delivery_manifests"
    )
    op.drop_table("delivery_manifests")
