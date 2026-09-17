"""SQLAlchemy models for the Workforce-owned database."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DriverApplicationRow(Base):
    __tablename__ = "driver_applications"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_driver_application_reference"),
        Index(
            "uq_driver_application_one_open",
            "applicant_principal_id",
            unique=True,
            postgresql_where="status IN ('SUBMITTED', 'AWAITING_OFFICE_VERIFICATION')",
        ),
        Index("ix_driver_application_status", "status"),
        # SEC-03 — a verified application always names the operator and office that
        # checked the documents in person. A verification nobody performed is not one.
        CheckConstraint(
            "status <> 'VERIFIED' OR ("
            "verified_by_actor_id IS NOT NULL AND verified_at_office IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="ck_driver_application_verification_is_attributed",
        ),
    )

    application_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    applicant_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    vehicle_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    vehicle_plate_number: Mapped[str] = mapped_column(String(16), nullable=False)
    vehicle_model: Mapped[str | None] = mapped_column(String(64))
    declared_shifts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    terms_version_accepted: Mapped[str | None] = mapped_column(String(32))
    documents_received: Mapped[list] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    documents_verified: Mapped[list] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verified_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    verified_at_office: Mapped[str | None] = mapped_column(String(160))
    decision_reason: Mapped[str | None] = mapped_column(String(512))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DriverProfileRow(Base):
    __tablename__ = "drivers"
    __table_args__ = (
        UniqueConstraint("principal_id", name="uq_driver_principal"),
        # A driver exists only because an application was verified.
        UniqueConstraint("application_id", name="uq_driver_application"),
        Index("ix_driver_status", "status"),
    )

    driver_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    application_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    vehicle_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    vehicle_plate_number: Mapped[str] = mapped_column(String(16), nullable=False)
    vehicle_model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    activated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ShiftPatternRow(Base):
    __tablename__ = "driver_shift_patterns"
    __table_args__ = (
        Index("ix_shift_pattern_driver", "driver_id", "effective_from"),
        # DRV-A04 — "Pick at least one shift".
        CheckConstraint(
            "jsonb_array_length(windows) >= 1", name="ck_shift_pattern_has_a_window"
        ),
        # A pattern that ended before it began would make today's shift unknowable.
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_shift_pattern_window_ordered",
        ),
    )

    pattern_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    windows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    effective_from: Mapped[object] = mapped_column(Date, nullable=False)
    effective_to: Mapped[object | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AttendanceRow(Base):
    __tablename__ = "driver_attendance"
    __table_args__ = (
        UniqueConstraint("driver_id", "shift_date", name="uq_attendance_driver_day"),
        Index("ix_attendance_driver", "driver_id", "shift_date"),
        CheckConstraint(
            "lateness_minutes >= 0", name="ck_attendance_lateness_not_negative"
        ),
        # "Starting a shift records your attendance" — a started shift has a start time.
        CheckConstraint(
            "status <> 'STARTED' OR started_at IS NOT NULL",
            name="ck_attendance_started_has_time",
        ),
    )

    attendance_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shift_date: Mapped[object] = mapped_column(Date, nullable=False)
    scheduled_start: Mapped[object] = mapped_column(Time, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    lateness_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LatenessBlockRow(Base):
    __tablename__ = "driver_lateness_blocks"
    __table_args__ = (
        # One open block per driver: two would each need clearing separately, and a
        # driver would stay paused after support thought they had fixed it.
        Index(
            "uq_lateness_block_one_active",
            "driver_id",
            unique=True,
            postgresql_where="cleared_at IS NULL",
        ),
        # OPS-09 — a cleared block always names who cleared it.
        CheckConstraint(
            "(cleared_at IS NULL) = (cleared_by_actor_id IS NULL)",
            name="ck_lateness_block_clearing_is_attributed",
        ),
        CheckConstraint("delay_minutes >= 0", name="ck_lateness_block_delay_not_negative"),
    )

    block_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    shift_date: Mapped[object] = mapped_column(Date, nullable=False)
    scheduled_start: Mapped[object] = mapped_column(Time, nullable=False)
    opened_app_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_since: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    unblock_requested_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cleared_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    clearing_note: Mapped[str | None] = mapped_column(String(512))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LeaveRequestRow(Base):
    __tablename__ = "driver_leave_requests"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_leave_reference"),
        Index("ix_leave_driver", "driver_id", "start_date"),
        Index("ix_leave_pending", "status", postgresql_where="status = 'PENDING'"),
        CheckConstraint("end_date >= start_date", name="ck_leave_window_ordered"),
        # Hourly leave states its hours and sits inside one day; full-day leave does not.
        CheckConstraint(
            "(kind = 'HOURLY') = (starts_at IS NOT NULL AND ends_at IS NOT NULL)",
            name="ck_leave_hours_match_kind",
        ),
        CheckConstraint(
            "kind <> 'HOURLY' OR start_date = end_date",
            name="ck_leave_hourly_is_one_day",
        ),
        # A decline must say why — the driver has to know what to do next.
        CheckConstraint(
            "status <> 'DECLINED' OR decision_note IS NOT NULL",
            name="ck_leave_decline_has_reason",
        ),
    )

    leave_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reference: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[object] = mapped_column(Date, nullable=False)
    end_date: Mapped[object] = mapped_column(Date, nullable=False)
    starts_at: Mapped[object | None] = mapped_column(Time)
    ends_at: Mapped[object | None] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1024))
    requested_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    decision_note: Mapped[str | None] = mapped_column(String(1024))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationInboxRow(Base):
    """Workforce-owned durable inbox (ADR-0008).

    There is no matching outbox: Workforce answers questions over HTTP and publishes none.
    """

    __tablename__ = "workforce_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_workforce_inbox_consumer_event"
        ),
        Index("ix_workforce_inbox_status", "status", "processing_lease_until"),
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
