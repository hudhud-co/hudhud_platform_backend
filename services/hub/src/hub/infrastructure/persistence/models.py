"""SQLAlchemy models for the Hub-owned database."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class HubRow(Base):
    __tablename__ = "hubs"
    __table_args__ = (UniqueConstraint("code", name="uq_hub_code"),)

    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    #: v6.3 p.23 — per hub, never company-wide. A column, not a constant.
    cut_off_local_time: Mapped[object] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    vehicle_cameras_fitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DropOffRow(Base):
    __tablename__ = "hub_drop_offs"
    __table_args__ = (
        # One live drop-off per tracking code: two would mean two custody starts.
        Index(
            "uq_hub_drop_off_live_tracking_code",
            "tracking_code",
            unique=True,
            postgresql_where="status IN ('EXPECTED', 'DETAILS_CAPTURED', 'LABELLED')",
        ),
        Index(
            "uq_hub_drop_off_label_code",
            "label_code",
            unique=True,
            postgresql_where="label_code IS NOT NULL AND status <> 'CANCELLED'",
        ),
        Index("ix_hub_drop_off_hub_status", "hub_id", "status"),
        # CUS-05 and CUS-07: an accepted drop-off carries the label and weight hub staff
        # recorded, and names the operator who took custody (SEC-09).
        CheckConstraint(
            "status <> 'ACCEPTED' OR ("
            "label_code IS NOT NULL AND weight_grams IS NOT NULL "
            "AND accepted_by_actor_id IS NOT NULL)",
            name="ck_hub_drop_off_accepted_is_complete",
        ),
        CheckConstraint(
            "weight_grams IS NULL OR weight_grams > 0",
            name="ck_hub_drop_off_weight_positive",
        ),
    )

    drop_off_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    shipment_request_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    sender_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    captured_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    label_code: Mapped[str | None] = mapped_column(String(32))
    labelled_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    accepted_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ParcelPresenceRow(Base):
    __tablename__ = "hub_parcel_presences"
    __table_args__ = (
        UniqueConstraint("hub_id", "tracking_code", name="uq_hub_presence_parcel"),
        Index("ix_hub_presence_hub_status", "hub_id", "status"),
        Index("ix_hub_presence_consignment", "consignment_id"),
        # A held parcel always says why, so nothing is stopped without a reason on record.
        CheckConstraint(
            "status <> 'HELD' OR hold_reason IS NOT NULL",
            name="ck_hub_presence_held_has_reason",
        ),
    )

    presence_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    routing_decision: Mapped[str | None] = mapped_column(String(32))
    route_code: Mapped[str | None] = mapped_column(String(32))
    consignment_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    received_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    sorted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    departed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    handed_to_last_mile_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    hold_reason: Mapped[str | None] = mapped_column(String(40))
    disposition: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ConsignmentRow(Base):
    __tablename__ = "hub_consignments"
    __table_args__ = (
        Index(
            "uq_hub_consignment_seal_code",
            "seal_code",
            unique=True,
            postgresql_where="seal_code IS NOT NULL",
        ),
        Index("ix_hub_consignment_origin", "origin_hub_id", "status"),
        Index("ix_hub_consignment_destination", "destination_hub_id", "status"),
        # A sealed consignment records who applied the seal and when.
        CheckConstraint(
            "(seal_code IS NULL) = (seal_applied_at IS NULL)",
            name="ck_hub_consignment_seal_pair",
        ),
        CheckConstraint(
            "seal_code IS NULL OR seal_applied_by_actor_id IS NOT NULL",
            name="ck_hub_consignment_seal_attributed",
        ),
        CheckConstraint(
            "origin_hub_id <> destination_hub_id",
            name="ck_hub_consignment_between_two_hubs",
        ),
    )

    consignment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    origin_hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    destination_hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    seal_code: Mapped[str | None] = mapped_column(String(32))
    seal_applied_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    seal_applied_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    parcel_codes: Mapped[list] = mapped_column(
        ARRAY(String(24)), nullable=False, default=list
    )
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SealCheckRow(Base):
    """Append-only: a seal check is evidence, and evidence is not edited."""

    __tablename__ = "hub_seal_checks"
    __table_args__ = (Index("ix_hub_seal_check_consignment", "consignment_id"),)

    check_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consignment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    expected_seal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_seal_code: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    checked_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_by_actor_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class LinehaulRow(Base):
    __tablename__ = "hub_linehauls"
    __table_args__ = (
        UniqueConstraint("consignment_id", name="uq_hub_linehaul_consignment"),
        Index("ix_hub_linehaul_origin", "origin_hub_id", "status"),
        CheckConstraint(
            "origin_hub_id <> destination_hub_id",
            name="ck_hub_linehaul_between_two_hubs",
        ),
    )

    linehaul_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consignment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    destination_hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    vehicle_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    driver_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    planned_departure_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    departed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    expected_arrival_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    route_deviation_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    deviation_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class VehiclePositionRow(Base):
    """Append-only location fixes, each attributed to the source that produced it."""

    __tablename__ = "hub_vehicle_positions"
    __table_args__ = (
        Index("ix_hub_position_linehaul", "linehaul_id", "recorded_at"),
    )

    position_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    linehaul_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    latitude: Mapped[object] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[object] = mapped_column(Numeric(9, 6), nullable=False)
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationOutboxRow(Base):
    """Hub-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "hub_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_hub_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id", "aggregate_version", name="uq_hub_outbox_aggregate_version"
        ),
        Index(
            "ix_hub_outbox_pending",
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


class IntegrationInboxRow(Base):
    """Hub-owned durable inbox (ADR-0008)."""

    __tablename__ = "hub_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_hub_inbox_consumer_event"
        ),
        Index("ix_hub_inbox_status", "status", "processing_lease_until"),
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
