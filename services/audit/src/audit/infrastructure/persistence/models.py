"""Service-owned SQLAlchemy metadata for Alembic migrations."""

from __future__ import annotations

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

metadata = MetaData()


class Base(DeclarativeBase):
    metadata = metadata


class IntegrationInboxRow(Base):
    """ADR-0008 inbox. Not a canonical Audit fact."""

    __tablename__ = "audit_integration_inbox"
    __table_args__ = (
        UniqueConstraint("consumer_name", "event_id", name="uq_audit_inbox_consumer_event"),
        Index("ix_audit_inbox_consumer_status", "consumer_name", "status"),
    )

    id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_owner: Mapped[str | None] = mapped_column(String(128))
    processing_lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    quarantined_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    jetstream_stream: Mapped[str | None] = mapped_column(String(128))
    jetstream_seq: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    nats_msg_id: Mapped[str | None] = mapped_column(String(128))
    processing_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class LegacyAuditObservationRow(Base):
    """Legacy A2 observation projection — not a canonical Audit fact."""

    __tablename__ = "legacy_audit_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_table",
            "source_pk",
            name="uq_legacy_audit_observation_source_row",
        ),
        Index("ix_legacy_audit_observation_audit_entry_id", "audit_entry_id"),
        Index("ix_legacy_audit_observation_entity", "entity_type", "entity_id"),
        Index("ix_legacy_audit_observation_occurred_at", "occurred_at"),
    )

    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_pk: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_position: Mapped[str] = mapped_column(String(256), nullable=False)
    source_module: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_entry_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    bridge_mapper_version: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
