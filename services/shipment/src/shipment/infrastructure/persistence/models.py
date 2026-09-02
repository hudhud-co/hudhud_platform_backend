"""Service-owned SQLAlchemy metadata for Shipment acceptance persistence."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

metadata = MetaData()


class Base(DeclarativeBase):
    metadata = metadata


class OrderIntentRow(Base):
    __tablename__ = "order_intents"

    order_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ShipmentRow(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("waybill_number", name="uq_shipments_waybill_number"),
        Index("ix_shipments_order_id", "order_id"),
    )

    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    order_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    waybill_number: Mapped[str] = mapped_column(String(128), nullable=False)
    current_status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    sla_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    current_custody_type: Mapped[str | None] = mapped_column(String(32))
    current_custody_id: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PickupTaskSnapshotRow(Base):
    __tablename__ = "pickup_task_snapshots"
    __table_args__ = (
        UniqueConstraint("shipment_id", name="uq_pickup_task_snapshots_shipment_id"),
        Index("ix_pickup_task_snapshots_shipment_id", "shipment_id"),
    )

    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_driver_user_id: Mapped[str | None] = mapped_column(String(128))
    assigned_batch_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    has_pickup_condition_proof: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acceptance_state: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ShipmentEventRow(Base):
    __tablename__ = "shipment_events"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id",
            "event_type",
            name="uq_shipment_events_shipment_event_type",
        ),
        Index("ix_shipment_events_shipment_id", "shipment_id"),
    )

    event_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AcceptanceAuditLogRow(Base):
    __tablename__ = "acceptance_audit_logs"
    __table_args__ = (
        Index("ix_acceptance_audit_logs_entity", "entity_type", "entity_id"),
    )

    audit_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)


class AcceptanceDecisionRow(Base):
    __tablename__ = "acceptance_decisions"
    __table_args__ = (
        UniqueConstraint("shipment_id", name="uq_acceptance_decisions_shipment_id"),
        UniqueConstraint("pickup_task_id", name="uq_acceptance_decisions_pickup_task_id"),
    )

    decision_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    acting_driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scanned_identifier: Mapped[str] = mapped_column(String(256), nullable=False)
    scan_timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    exception_evidence: Mapped[list[dict[str, str | bool | None]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
