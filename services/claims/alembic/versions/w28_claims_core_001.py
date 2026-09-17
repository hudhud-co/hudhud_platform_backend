"""W28: Claims core — compensation claims, the support thread, and driver incidents.

Expand-only: this migration creates new tables and touches nothing that exists.

The rules carried into the database are the ones a concurrent request could otherwise
break. A second claim cannot be opened on a parcel that already has one open, however
many support agents file at the same instant. A claim cannot be approved or rejected
unless the custody review was recorded (CLM-03). An approved claim always carries an
amount and the person who decided it; a rejected one always carries a reason (CLM-04).
An amount exists only on an approved claim, because before the decision there is no
figure and a stored one would look like a decision nobody made.

Two absences are deliberate and are asserted by a test rather than left to review.

* **``claims_driver_incidents`` has no compensation column.** SEC-07: "No compensation
  or claim value is shown to the driver", and Driver App v8's report screen adds "No
  amounts are shown or decided here." An incident that should cost money becomes a
  claim, and ``linked_claim_id`` points at it; the money lives there.
* **There is no return-window table.** CLM-06: there is no return window after
  acceptance at the door, so there is no state to keep.

Money is integer minor units with an explicit currency (ADR-0012). There is no NUMERIC
and no float on any column in this migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w28_claims_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims_compensation_claims",
        sa.Column("claim_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("tracking_code", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("opened_by", sa.String(length=16), nullable=False),
        sa.Column("opened_by_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("sender_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("merchant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("custody_boundary", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "custody_records_reviewed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("compensation_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("compensation_currency", sa.String(length=3), nullable=True),
        sa.Column("rejection_reason", sa.String(length=48), nullable=True),
        sa.Column("rejection_note", sa.Text(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("reference", name="uq_claims_claim_reference"),
        # CLM-03 — "Hudhud reviews scan and custody records before compensating".
        sa.CheckConstraint(
            "status NOT IN ('APPROVED', 'REJECTED') OR custody_records_reviewed",
            name="ck_claims_claim_decided_after_custody_review",
        ),
        # CLM-04 — approved ⇒ an amount and a decider; rejected ⇒ a documented reason.
        sa.CheckConstraint(
            "status <> 'APPROVED' OR ("
            "compensation_minor_units IS NOT NULL "
            "AND compensation_currency IS NOT NULL "
            "AND decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_claims_claim_approved_carries_an_amount",
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR ("
            "rejection_reason IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_claims_claim_rejected_carries_a_reason",
        ),
        sa.CheckConstraint(
            "compensation_minor_units IS NULL OR status = 'APPROVED'",
            name="ck_claims_claim_amount_only_when_approved",
        ),
        sa.CheckConstraint(
            "(compensation_minor_units IS NULL) = (compensation_currency IS NULL)",
            name="ck_claims_claim_amount_has_currency",
        ),
        sa.CheckConstraint(
            "compensation_minor_units IS NULL OR compensation_minor_units > 0",
            name="ck_claims_claim_amount_is_positive",
        ),
        # v6.3 p.37 — a claim only exists where HUDHUD still carried the risk. Once the
        # receiver took the parcel inside to test it, the liability had ended.
        sa.CheckConstraint(
            "custody_boundary IN ('IN_HUDHUD_CUSTODY', 'OPEN_BOX_AT_THE_DOOR')",
            name="ck_claims_claim_filed_within_hudhud_liability",
        ),
        sa.CheckConstraint("version >= 1", name="ck_claims_claim_version_positive"),
    )
    # CLM-02 — anyone of three people may open one, but not a second while the first is
    # still open. A partial unique index, because two agents filing simultaneously is
    # precisely the case an application-level check lets through.
    op.create_index(
        "uq_claims_one_open_per_parcel",
        "claims_compensation_claims",
        ["tracking_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('SUBMITTED', 'UNDER_REVIEW')"),
    )
    op.create_index(
        "ix_claims_claim_opened_by_principal",
        "claims_compensation_claims",
        ["opened_by_principal_id"],
    )
    op.create_index(
        "ix_claims_claim_sender_principal",
        "claims_compensation_claims",
        ["sender_principal_id"],
    )
    op.create_index(
        "ix_claims_claim_status",
        "claims_compensation_claims",
        ["status", "submitted_at"],
    )

    op.create_table(
        "claims_messages",
        sa.Column("message_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("claim_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("author", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "attachments_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(
            "author IN ('CLAIMANT', 'SUPPORT')", name="ck_claims_message_author"
        ),
        sa.CheckConstraint(
            "char_length(btrim(body)) > 0", name="ck_claims_message_body_not_blank"
        ),
    )
    op.create_index(
        "ix_claims_message_claim", "claims_messages", ["claim_id", "written_at"]
    )

    op.create_table(
        "claims_driver_incidents",
        sa.Column("incident_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("reported_by_driver_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tracking_code", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("investigation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_actor_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("linked_claim_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("reference", name="uq_claims_incident_reference"),
        # Driver App v8 `needsParcel` — a breakdown is about the driver, everything
        # else is about one parcel.
        sa.CheckConstraint(
            "(kind = 'VEHICLE_OR_SAFETY_ISSUE') = (tracking_code IS NULL)",
            name="ck_claims_incident_parcel_matches_kind",
        ),
        # OPS-07 — operations closes it, and says what was found.
        sa.CheckConstraint(
            "status <> 'RESOLVED' OR ("
            "resolved_at IS NOT NULL AND resolved_by_actor_id IS NOT NULL "
            "AND char_length(btrim(coalesce(resolution_note, ''))) > 0)",
            name="ck_claims_incident_resolved_is_documented",
        ),
        sa.CheckConstraint(
            "status = 'RESOLVED' OR resolved_at IS NULL",
            name="ck_claims_incident_only_resolved_has_resolved_at",
        ),
    )
    op.create_index(
        "ix_claims_incident_driver",
        "claims_driver_incidents",
        ["reported_by_driver_id", "reported_at"],
    )
    op.create_index(
        "ix_claims_incident_open",
        "claims_driver_incidents",
        ["status"],
        postgresql_where=sa.text("status <> 'RESOLVED'"),
    )

    # One series for claims and incidents together: both are shown as ``CLM-…`` and the
    # OPS-06 view lists them side by side, so two different things must never wear the
    # same reference.
    op.create_table(
        "claims_reference_sequences",
        sa.Column("scope", sa.String(length=16), primary_key=True),
        sa.Column("day", sa.String(length=8), primary_key=True),
        sa.Column(
            "last_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )

    op.create_table(
        "claims_integration_outbox",
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
        sa.UniqueConstraint("event_id", name="uq_claims_outbox_event_id"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_claims_outbox_aggregate_version",
        ),
    )
    op.create_index(
        "ix_claims_outbox_pending",
        "claims_integration_outbox",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "claims_integration_inbox",
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
            "consumer_name", "event_id", name="uq_claims_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_claims_inbox_status",
        "claims_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_claims_inbox_status", table_name="claims_integration_inbox")
    op.drop_table("claims_integration_inbox")
    op.drop_index("ix_claims_outbox_pending", table_name="claims_integration_outbox")
    op.drop_table("claims_integration_outbox")
    op.drop_table("claims_reference_sequences")
    op.drop_index("ix_claims_incident_open", table_name="claims_driver_incidents")
    op.drop_index("ix_claims_incident_driver", table_name="claims_driver_incidents")
    op.drop_table("claims_driver_incidents")
    op.drop_index("ix_claims_message_claim", table_name="claims_messages")
    op.drop_table("claims_messages")
    op.drop_index("ix_claims_claim_status", table_name="claims_compensation_claims")
    op.drop_index(
        "ix_claims_claim_sender_principal", table_name="claims_compensation_claims"
    )
    op.drop_index(
        "ix_claims_claim_opened_by_principal", table_name="claims_compensation_claims"
    )
    op.drop_index(
        "uq_claims_one_open_per_parcel", table_name="claims_compensation_claims"
    )
    op.drop_table("claims_compensation_claims")
