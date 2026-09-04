"""Service-owned SQLAlchemy metadata for Pickup recovery and acceptance persistence."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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


class PickupTaskRow(Base):
    """Persisted pickup attempt with lineage — owned by Pickup service."""

    __tablename__ = "pickup_tasks"
    __table_args__ = (
        UniqueConstraint(
            "root_attempt_id",
            "attempt_number",
            name="uq_pickup_tasks_root_attempt_number",
        ),
        Index("ix_pickup_tasks_shipment_id", "shipment_id"),
        Index("ix_pickup_tasks_root_attempt_id", "root_attempt_id"),
    )

    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assigned_driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_batch_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    root_attempt_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    parent_attempt_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    superseded_by_task_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    scheduled_window_start: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    scheduled_window_end: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    acceptance_state: Mapped[str | None] = mapped_column(String(32))
    has_pickup_condition_proof: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    accepted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    accepted_by_driver_user_id: Mapped[str | None] = mapped_column(String(128))
    recovery_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    recovered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RecoveryHistoryRow(Base):
    """Append-only recovery audit record."""

    __tablename__ = "pickup_recovery_history"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pickup_recovery_history_idempotency_key"),
        Index("ix_pickup_recovery_history_pickup_task_id", "pickup_task_id"),
    )

    history_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    replacement_task_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryIdempotencyRow(Base):
    """Recovery command idempotency outcome — one result per key."""

    __tablename__ = "pickup_recovery_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    original_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    result_task_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AcceptanceIdempotencyRow(Base):
    """Acceptance command idempotency outcome — one result and event_id per key."""

    __tablename__ = "pickup_acceptance_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationOutboxRow(Base):
    """Pickup-owned transactional integration outbox (ADR-0008 / Bridge shape)."""

    __tablename__ = "pickup_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_pickup_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_pickup_outbox_aggregate_version",
        ),
        UniqueConstraint(
            "event_type",
            "aggregate_id",
            name="uq_pickup_outbox_event_type_aggregate",
        ),
        Index(
            "ix_pickup_outbox_pending",
            "status",
            "next_attempt_at",
            postgresql_where="status IN ('pending', 'processing')",
        ),
    )

    id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
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
