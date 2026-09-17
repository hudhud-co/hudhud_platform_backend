"""W24: Notification core — recipients, preferences, notifications, centre, inbox.

Expand-only: this migration creates new tables and touches nothing that exists.

Two rules are carried by the database rather than by service code alone:

* a recipient is stored as a 64-character keyed digest, never as a phone number;
* the receiver's acceptance SMS can never be switched off, because it carries the
  delivery code (NTF-03, v6.3 p.20). The check constraint means a direct write cannot
  create an opt-out the service layer would have refused.

There is no outbox table: Notification consumes journey facts and publishes none.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w24_notification_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_recipients",
        sa.Column("recipient_key", sa.String(length=64), primary_key=True),
        sa.Column("phone_last4", sa.String(length=4), nullable=False),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "app_installed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "whatsapp_available", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("locale", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "length(recipient_key) = 64", name="ck_notification_recipient_key_is_digest"
        ),
        sa.CheckConstraint(
            "length(phone_last4) = 4", name="ck_notification_recipient_last4"
        ),
    )
    op.create_index(
        "ix_notification_recipient_principal",
        "notification_recipients",
        ["principal_id"],
    )

    op.create_table(
        "notification_preferences",
        sa.Column("preference_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "principal_id", "category", "channel", name="uq_notification_preference_scope"
        ),
        sa.CheckConstraint(
            "NOT (category = 'ACCEPTANCE' AND channel = 'SMS' AND enabled = false)",
            name="ck_notification_acceptance_sms_always_on",
        ),
    )

    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("recipient_key", sa.String(length=64), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("template_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("tracking_code", sa.String(length=24), nullable=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),
        sa.CheckConstraint(
            "length(recipient_key) = 64", name="ck_notification_recipient_key_is_digest"
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_notification_failure_has_reason",
        ),
    )
    op.create_index(
        "ix_notification_pending",
        "notifications",
        ["status"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index("ix_notification_tracking_code", "notifications", ["tracking_code"])
    op.create_index("ix_notification_principal", "notifications", ["principal_id"])

    op.create_table(
        "notification_centre_entries",
        sa.Column("entry_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("tracking_code", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_notification_centre_principal",
        "notification_centre_entries",
        ["principal_id", "created_at"],
    )
    op.create_index(
        "ix_notification_centre_unread",
        "notification_centre_entries",
        ["principal_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.create_table(
        "notification_integration_inbox",
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
            "consumer_name", "event_id", name="uq_notification_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_notification_inbox_status",
        "notification_integration_inbox",
        ["status", "processing_lease_until"],
    )


def downgrade() -> None:
    op.drop_table("notification_integration_inbox")
    op.drop_table("notification_centre_entries")
    op.drop_table("notifications")
    op.drop_table("notification_preferences")
    op.drop_table("notification_recipients")
