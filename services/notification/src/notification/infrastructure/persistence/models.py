"""SQLAlchemy models for the Notification-owned database.

No notification row holds a phone number, a rendered message body, or a delivery code.
What it holds is a keyed digest of the recipient, the last four digits for display, and
enough metadata to deduplicate, retry and explain what happened.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RecipientProfileRow(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        Index("ix_notification_recipient_principal", "principal_id"),
        # A 64-character hex digest, never a phone number.
        CheckConstraint(
            "length(recipient_key) = 64", name="ck_notification_recipient_key_is_digest"
        ),
        CheckConstraint(
            "length(phone_last4) = 4", name="ck_notification_recipient_last4"
        ),
    )

    recipient_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    app_installed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whatsapp_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ChannelPreferenceRow(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "category",
            "channel",
            name="uq_notification_preference_scope",
        ),
        # NTF-03 — the receiver's acceptance SMS carries the delivery code and can never
        # be switched off. Enforced here as well as in the service so a direct write
        # cannot create an opt-out the service would have refused.
        CheckConstraint(
            "NOT (category = 'ACCEPTANCE' AND channel = 'SMS' AND enabled = false)",
            name="ck_notification_acceptance_sms_always_on",
        ),
    )

    preference_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The idempotency key. One triggering fact, one recipient, one channel, one
        # message — however many times the broker redelivers.
        UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),
        Index("ix_notification_pending", "status", postgresql_where="status = 'PENDING'"),
        Index("ix_notification_tracking_code", "tracking_code"),
        Index("ix_notification_principal", "principal_id"),
        CheckConstraint(
            "length(recipient_key) = 64", name="ck_notification_recipient_key_is_digest"
        ),
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_notification_failure_has_reason",
        ),
    )

    notification_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    recipient_key: Mapped[str] = mapped_column(String(64), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_code: Mapped[str | None] = mapped_column(String(24))
    principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    #: Non-secret render values only — a tracking link, an ETA. Never a delivery code.
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CentreEntryRow(Base):
    __tablename__ = "notification_centre_entries"
    __table_args__ = (
        Index("ix_notification_centre_principal", "principal_id", "created_at"),
        Index(
            "ix_notification_centre_unread",
            "principal_id",
            postgresql_where="read_at IS NULL",
        ),
    )

    entry_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    tracking_code: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationInboxRow(Base):
    """Notification-owned durable inbox (ADR-0008).

    There is no matching outbox: this service consumes journey facts and publishes none.
    """

    __tablename__ = "notification_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_notification_inbox_consumer_event"
        ),
        Index("ix_notification_inbox_status", "status", "processing_lease_until"),
    )

    inbox_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    processing_lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
