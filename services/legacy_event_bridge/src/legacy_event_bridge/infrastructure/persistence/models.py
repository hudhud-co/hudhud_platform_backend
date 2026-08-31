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


class BridgeLandingRow(Base):
    __tablename__ = "bridge_landing"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_table",
            "source_pk",
            name="uq_bridge_landing_source_row",
        ),
        Index("ix_bridge_landing_mapping_state", "mapping_state"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_pk: Mapped[str] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_position: Mapped[str] = mapped_column(String(256), nullable=False)
    mapper_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    mapping_state: Mapped[str] = mapped_column(String(32), nullable=False)
    mapping_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mapped_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    quarantined_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)


class BridgeCheckpointRow(Base):
    __tablename__ = "bridge_checkpoint"

    capture_source: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_durably_landed_position: Mapped[str | None] = mapped_column(String(256))
    last_feedback_eligible_position: Mapped[str | None] = mapped_column(String(256))
    last_external_slot_advanced_position: Mapped[str | None] = mapped_column(String(256))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class BridgeOutboxRow(Base):
    __tablename__ = "bridge_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_bridge_outbox_event_id"),
        Index(
            "ix_bridge_outbox_pending",
            "status",
            "next_attempt_at",
            postgresql_where="status IN ('pending', 'processing')",
        ),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_owner: Mapped[str | None] = mapped_column(String(128))
    processing_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    landing_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=True))
