"""SQLAlchemy models for the Delivery-owned database.

Two absences are deliberate and are asserted by tests rather than left to review.

* No column anywhere holds a **delivery code**. ``delivery_stops.delivery_code_digest``
  is a keyed HMAC bound to the stop; the code itself never reaches this database.
* No column anywhere holds an **identity-document photograph** (DRV-L07). Until the
  retention period is decided there is no field one could be written into, so no
  migration is needed to stop retaining something that was never retained.

Money is stored as integer minor units with an explicit currency. There is no NUMERIC
and no float on any monetary column.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DeliveryManifestRow(Base):
    """A last-mile driver's round (v6.3 p.25)."""

    __tablename__ = "delivery_manifests"
    __table_args__ = (
        Index(
            "uq_delivery_manifest_one_open_per_driver",
            "driver_principal_id",
            unique=True,
            postgresql_where="closed_at IS NULL",
        ),
        Index("ix_delivery_manifest_hub", "hub_id"),
    )

    manifest_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    driver_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    hub_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DeliveryStopRow(Base):
    """One parcel at one door."""

    __tablename__ = "delivery_stops"
    __table_args__ = (
        Index(
            "uq_delivery_stop_one_live_per_parcel",
            "tracking_code",
            unique=True,
            postgresql_where=(
                "status NOT IN ('DELIVERED', 'REFUSED', 'FAILED')"
            ),
        ),
        Index("ix_delivery_stop_manifest", "manifest_id", "created_at"),
        Index("ix_delivery_stop_driver_open", "driver_principal_id", "status"),
        # v6.3 p.26 — custody passes to the receiver only on delivery, and a delivered
        # stop always records how the person at the door was verified.
        CheckConstraint(
            "status <> 'DELIVERED' OR ("
            "verified_at IS NOT NULL AND verified_by_method IS NOT NULL "
            "AND delivered_at IS NOT NULL)",
            name="ck_delivery_stop_delivered_is_verified",
        ),
        # A closed stop has a closing time; an open one does not.
        CheckConstraint(
            "(status IN ('DELIVERED', 'REFUSED', 'FAILED')) = (closed_at IS NOT NULL)",
            name="ck_delivery_stop_closed_at_matches_status",
        ),
        # A refusal or a failed attempt collects nothing, so neither may claim delivery.
        CheckConstraint(
            "status = 'DELIVERED' OR delivered_at IS NULL",
            name="ck_delivery_stop_only_delivered_has_delivered_at",
        ),
        # DRV-L05 — the digest is 64 hex characters of HMAC-SHA256. A shorter value
        # would mean something other than a digest had been written here.
        CheckConstraint(
            "delivery_code_digest IS NULL OR char_length(delivery_code_digest) = 64",
            name="ck_delivery_stop_code_digest_is_a_digest",
        ),
        CheckConstraint(
            "cod_amount_minor_units IS NULL OR cod_amount_minor_units >= 0",
            name="ck_delivery_stop_cod_amount_not_negative",
        ),
        CheckConstraint(
            "(cod_amount_minor_units IS NULL) = (cod_amount_currency IS NULL)",
            name="ck_delivery_stop_cod_amount_has_currency",
        ),
        CheckConstraint(
            "code_attempt_count >= 0", name="ck_delivery_stop_attempts_not_negative"
        ),
    )

    stop_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    manifest_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    driver_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    open_box_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    photo_documentation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    packaging_seal_code: Mapped[str | None] = mapped_column(String(64))

    #: A keyed digest bound to this stop. Never the code.
    delivery_code_digest: Mapped[str | None] = mapped_column(String(64))
    named_receiver: Mapped[str | None] = mapped_column(String(160))
    cod_amount_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    cod_amount_currency: Mapped[str | None] = mapped_column(String(3))
    payment_method_expected: Mapped[str] = mapped_column(String(32), nullable=False)

    custody_taken_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    departed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    wait_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    verified_by_method: Mapped[str | None] = mapped_column(String(32))
    seal_outcome: Mapped[str | None] = mapped_column(String(32))
    inspection_outcome: Mapped[str | None] = mapped_column(String(32))
    delivered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    refusal_reason: Mapped[str | None] = mapped_column(String(32))
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    code_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class VerificationAttemptRow(Base):
    """One attempt to prove the person at the door may take the parcel.

    No typed code, no ID photograph, no document number: the method, the outcome, the
    actor and the time are the whole record.
    """

    __tablename__ = "delivery_verification_attempts"
    __table_args__ = (
        Index("ix_delivery_verification_stop", "stop_id", "attempted_at"),
    )

    attempt_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stop_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    attempted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    attempted_by_actor_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    id_matched_named_receiver: Mapped[bool | None] = mapped_column(Boolean)


class PaymentRecordRow(Base):
    """What was collected at the door (v6.3 p.31)."""

    __tablename__ = "delivery_payments"
    __table_args__ = (
        UniqueConstraint("stop_id", name="uq_delivery_payment_one_per_stop"),
        CheckConstraint(
            "amount_minor_units IS NULL OR amount_minor_units >= 0",
            name="ck_delivery_payment_amount_not_negative",
        ),
        CheckConstraint(
            "(amount_minor_units IS NULL) = (amount_currency IS NULL)",
            name="ck_delivery_payment_amount_has_currency",
        ),
        # Driver App v8 `lmPayApproved` — "PROOF OF PAYMENT — ONE IS REQUIRED".
        CheckConstraint(
            "method <> 'POS_CARD' OR outcome <> 'COLLECTED' OR ("
            "pos_reference IS NOT NULL OR pos_receipt_key IS NOT NULL)",
            name="ck_delivery_payment_pos_has_proof",
        ),
        CheckConstraint(
            "outcome <> 'COLLECTED' OR amount_minor_units IS NOT NULL",
            name="ck_delivery_payment_collected_has_amount",
        ),
    )

    payment_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stop_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    amount_currency: Mapped[str | None] = mapped_column(String(3))
    pos_reference: Mapped[str | None] = mapped_column(String(64))
    pos_receipt_bucket: Mapped[str | None] = mapped_column(String(128))
    pos_receipt_key: Mapped[str | None] = mapped_column(String(512))
    pos_receipt_content_type: Mapped[str | None] = mapped_column(String(128))
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))


class PhotoEvidenceRow(Base):
    """A delivery photo, held as a pointer (MER-11). Delivery never stores the bytes."""

    __tablename__ = "delivery_photo_evidence"
    __table_args__ = (
        UniqueConstraint("stop_id", "stage", name="uq_delivery_photo_stop_stage"),
    )

    photo_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stop_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    media_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    media_key: Mapped[str] = mapped_column(String(512), nullable=False)
    media_content_type: Mapped[str | None] = mapped_column(String(128))
    captured_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_by_actor_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )


class FailedAttemptRow(Base):
    """A door visit that ended without a handover (v6.3 p.28, OPS-08)."""

    __tablename__ = "delivery_failed_attempts"
    __table_args__ = (
        UniqueConstraint("stop_id", name="uq_delivery_failed_attempt_one_per_stop"),
        Index(
            "ix_delivery_failed_attempt_awaiting_operations",
            "recorded_at",
            postgresql_where="next_attempt_decision IS NULL",
        ),
        # A decision always names who made it and when. An unattributed disposition is
        # not an operations decision.
        CheckConstraint(
            "(next_attempt_decision IS NULL) = (decided_at IS NULL)",
            name="ck_delivery_failed_attempt_decision_has_time",
        ),
        CheckConstraint(
            "next_attempt_decision IS NULL OR decided_by_actor_id IS NOT NULL",
            name="ck_delivery_failed_attempt_decision_is_attributed",
        ),
    )

    attempt_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    stop_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by_actor_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    next_attempt_decision: Mapped[str | None] = mapped_column(String(32))
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ReceiverPreferenceRow(Base):
    """A receiver's handover window and exact location (CUS-11, v6.3 p.20)."""

    __tablename__ = "delivery_receiver_preferences"
    __table_args__ = (
        UniqueConstraint(
            "tracking_code", name="uq_delivery_receiver_preference_tracking_code"
        ),
        CheckConstraint(
            "(window_starts_at_hour IS NULL) = (window_ends_at_hour IS NULL)",
            name="ck_delivery_receiver_preference_window_is_complete",
        ),
        CheckConstraint(
            "window_starts_at_hour IS NULL OR ("
            "window_starts_at_hour BETWEEN 0 AND 23 "
            "AND window_ends_at_hour BETWEEN 1 AND 24 "
            "AND window_ends_at_hour > window_starts_at_hour)",
            name="ck_delivery_receiver_preference_window_lies_within_a_day",
        ),
        CheckConstraint(
            "(geo_latitude IS NULL) = (geo_longitude IS NULL)",
            name="ck_delivery_receiver_preference_geo_is_a_pair",
        ),
    )

    preference_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    window_starts_at_hour: Mapped[int | None] = mapped_column(Integer)
    window_ends_at_hour: Mapped[int | None] = mapped_column(Integer)
    address_line: Mapped[str | None] = mapped_column(String(256))
    landmark: Mapped[str | None] = mapped_column(String(256))
    # A coordinate is a measurement, not money: NUMERIC is exactly right here and the
    # money columns above remain integer minor units.
    geo_latitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    geo_longitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    set_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ParcelIssueReportRow(Base):
    """A problem reported against a parcel (CUS-12 by the receiver, DRV-L21 by the driver)."""

    __tablename__ = "delivery_issue_reports"
    __table_args__ = (
        Index("ix_delivery_issue_tracking_code", "tracking_code", "reported_at"),
        Index("ix_delivery_issue_source", "source", "reported_at"),
        # DRV-L21 — a driver raises an incident *from a stop*, so it names one. A
        # receiver may report after delivery, when there is no live stop to name.
        CheckConstraint(
            "source <> 'DRIVER' OR stop_id IS NOT NULL",
            name="ck_delivery_issue_driver_report_names_a_stop",
        ),
    )

    report_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    stop_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    detail: Mapped[str | None] = mapped_column(Text)
    reported_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    #: A list of ``{bucket, key, content_type}`` pointers. Never the bytes.
    media_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reported_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CourierRatingRow(Base):
    """A private courier rating (CUS-13, SEC-08).

    The rater and the note live here and leave only as an aggregate. Nothing that reads
    this table on a courier's behalf selects either column.
    """

    __tablename__ = "delivery_courier_ratings"
    __table_args__ = (
        UniqueConstraint("tracking_code", name="uq_delivery_rating_one_per_parcel"),
        Index("ix_delivery_rating_courier", "courier_principal_id"),
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_delivery_rating_score_range"),
    )

    rating_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    courier_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    rated_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    rated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationOutboxRow(Base):
    """Delivery-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "delivery_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_delivery_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_delivery_outbox_aggregate_version",
        ),
        Index(
            "ix_delivery_outbox_pending",
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
    next_attempt_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processing_owner: Mapped[str | None] = mapped_column(String(128))
    processing_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationInboxRow(Base):
    """Delivery-owned durable inbox (ADR-0008)."""

    __tablename__ = "delivery_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_delivery_inbox_consumer_event"
        ),
        Index("ix_delivery_inbox_status", "status", "processing_lease_until"),
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
    processing_lease_until: Mapped[object | None] = mapped_column(
        DateTime(timezone=True)
    )
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
