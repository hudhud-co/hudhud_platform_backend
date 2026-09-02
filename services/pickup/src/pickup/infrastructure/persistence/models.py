"""Service-owned SQLAlchemy metadata for Pickup recovery persistence."""

from __future__ import annotations

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    Uuid,
)
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
