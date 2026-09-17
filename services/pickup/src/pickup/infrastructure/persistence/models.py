"""Service-owned SQLAlchemy metadata for Pickup recovery and acceptance persistence."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
        # The driver workload query runs on every work-session command.
        Index("ix_pickup_tasks_driver_status", "assigned_driver_user_id", "status"),
        # Stop readiness counts every expected parcel at one merchant stop.
        Index("ix_pickup_tasks_batch_outcome", "assigned_batch_id", "stop_outcome"),
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
    assignment_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OFFERED"
    )
    declined_reason: Mapped[str | None] = mapped_column(String(64))
    declined_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    scanned_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    scanned_identifier: Mapped[str | None] = mapped_column(String(256))
    condition_proof_captured_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True)
    )
    package_condition_status: Mapped[str | None] = mapped_column(String(32))
    packaging_assessment: Mapped[str | None] = mapped_column(String(32))
    condition_decision: Mapped[str | None] = mapped_column(String(32))
    photo_documentation_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    stop_outcome: Mapped[str | None] = mapped_column(String(32))
    stop_outcome_reason: Mapped[str | None] = mapped_column(String(64))
    stop_outcome_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    exception_reason: Mapped[str | None] = mapped_column(String(64))
    exception_reported_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
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


class TaskHistoryRow(Base):
    """Append-only Pickup audit history with explicit actor identity."""

    __tablename__ = "pickup_task_history"
    __table_args__ = (
        Index("ix_pickup_task_history_task", "pickup_task_id", "occurred_at"),
        Index("ix_pickup_task_history_actor", "actor_id"),
    )

    history_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DriverWorkSessionRow(Base):
    """Driver pickup-capability availability window."""

    __tablename__ = "pickup_driver_work_sessions"
    __table_args__ = (
        # At most one open session per driver — enforced in the database, not in code.
        Index(
            "uq_pickup_work_session_open_driver",
            "driver_user_id",
            unique=True,
            postgresql_where="status IN ('ACTIVE', 'PAUSED')",
        ),
        Index("ix_pickup_work_session_driver", "driver_user_id", "started_at"),
    )

    session_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    availability: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    home_hub_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    paused_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(String(32))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(String(512))
    session_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CourierChallengeRow(Base):
    """Assignment-bound sender verification challenge. Only the keyed hash is stored."""

    __tablename__ = "pickup_courier_challenges"
    __table_args__ = (
        Index("ix_pickup_courier_challenge_task", "pickup_task_id", "status"),
        Index(
            "uq_pickup_courier_challenge_open_task",
            "pickup_task_id",
            unique=True,
            postgresql_where="status IN ('ISSUED', 'VERIFIED')",
        ),
    )

    challenge_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assigned_driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(32), nullable=False)
    assignment_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    failed_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verification_valid_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verifier_user_id: Mapped[str | None] = mapped_column(String(128))
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(32))


class CourierManifestRow(Base):
    """Parcel manifest the courier submits and the sender confirms."""

    __tablename__ = "pickup_courier_manifests"
    __table_args__ = (
        Index("ix_pickup_courier_manifest_task", "pickup_task_id", "status"),
        Index(
            "uq_pickup_courier_manifest_open_task",
            "pickup_task_id",
            unique=True,
            postgresql_where="status IN ('SUBMITTED', 'CONFIRMED')",
        ),
    )

    manifest_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    confirmation_valid_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(32))


class HandoverManifestRow(Base):
    """Driver-declared set of parcels released to one origin hub."""

    __tablename__ = "pickup_handover_manifests"
    __table_args__ = (
        UniqueConstraint("manifest_code", name="uq_pickup_handover_manifest_code"),
        Index("ix_pickup_handover_manifest_driver", "driver_user_id", "status"),
        CheckConstraint("expected_count >= 0", name="ck_pickup_handover_expected_count"),
    )

    manifest_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    manifest_code: Mapped[str] = mapped_column(String(32), nullable=False)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discrepancy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    arrived_at_hub_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(512))
    manifest_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class HandoverManifestItemRow(Base):
    """One shipment line on a hub handover manifest."""

    __tablename__ = "pickup_handover_manifest_items"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "shipment_id",
            name="uq_pickup_handover_item_manifest_shipment",
        ),
        # A parcel may sit on only one non-terminal manifest at a time.
        Index(
            "uq_pickup_handover_item_open_shipment",
            "shipment_id",
            unique=True,
            postgresql_where="status IN ('EXPECTED')",
        ),
        Index("ix_pickup_handover_item_shipment", "shipment_id", "status"),
    )

    item_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    manifest_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    pickup_task_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    shipment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    added_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    received_by_user_id: Mapped[str | None] = mapped_column(String(128))
    received_hub_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    discrepancy_reason: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(String(512))


class OfflineAuthorizationRow(Base):
    """Signed, device-bound, time-boxed offline work authority."""

    __tablename__ = "pickup_offline_authorizations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_pickup_offline_authorization_token"),
        Index("ix_pickup_offline_authorization_driver", "driver_user_id", "issued_at"),
        Index("ix_pickup_offline_authorization_resource", "resource_id"),
    )

    authorization_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assignment_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    permitted_operations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    sync_deadline: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(256))


class OfflineStreamRow(Base):
    """Per-device append-only capture stream with contiguous sequencing."""

    __tablename__ = "pickup_offline_streams"
    __table_args__ = (
        Index("ix_pickup_offline_stream_driver", "driver_user_id"),
    )

    stream_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    authorization_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    last_contiguous_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_received_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class OfflineEventRow(Base):
    """Preserved offline capture — every submission is retained, applied or not."""

    __tablename__ = "pickup_offline_events"
    __table_args__ = (
        UniqueConstraint(
            "driver_user_id",
            "operation_id",
            name="uq_pickup_offline_event_driver_operation",
        ),
        UniqueConstraint(
            "stream_id",
            "sequence",
            name="uq_pickup_offline_event_stream_sequence",
        ),
        Index("ix_pickup_offline_event_driver_status", "driver_user_id", "status"),
        Index("ix_pickup_offline_event_resource", "resource_id"),
    )

    event_row_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stream_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    authorization_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assignment_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_code: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    processed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReconciliationCaseRow(Base):
    """Operations-owned case for an offline capture the server could not apply."""

    __tablename__ = "pickup_offline_reconciliation_cases"
    __table_args__ = (
        Index(
            "uq_pickup_reconciliation_open_event",
            "offline_event_row_id",
            unique=True,
            postgresql_where="status = 'OPEN'",
        ),
        Index("ix_pickup_reconciliation_driver_status", "driver_user_id", "status"),
        Index("ix_pickup_reconciliation_custody", "custody_implication", "status"),
    )

    case_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    offline_event_row_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    driver_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False)
    authoritative_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    submitted_event: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    custody_implication: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(128))
    resolution: Mapped[str | None] = mapped_column(String(64))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
