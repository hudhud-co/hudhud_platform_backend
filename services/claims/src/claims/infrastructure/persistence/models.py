"""SQLAlchemy models for the Claims-owned database.

Three things about this schema are decisions rather than mechanics.

**There is no compensation column on an incident.** SEC-07 says a driver is never shown
a compensation or claim value, and Driver App v8 says amounts are not decided on the
report screen. The absence is enforced here, in the table, so no query against
``claims_driver_incidents`` can return an amount however it is written. A test asserts it.

**A claim's amount is an integer with an explicit currency** (ADR-0012). No NUMERIC and
no float on any monetary column: 450,000 IQD split three ways must not drift.

**One open claim per parcel** is a partial unique index, not an application check. Two
support agents filing at once is exactly the case an application check misses.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CompensationClaimRow(Base):
    """One claim about one parcel (CLM-01 … CLM-06)."""

    __tablename__ = "claims_compensation_claims"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_claims_claim_reference"),
        # CLM-02 — anyone of three people may open one, but not a second while the
        # first is still open. A partial unique index, because two agents filing
        # simultaneously is precisely what an application-level check lets through.
        Index(
            "uq_claims_one_open_per_parcel",
            "tracking_code",
            unique=True,
            postgresql_where="status IN ('SUBMITTED', 'UNDER_REVIEW')",
        ),
        Index("ix_claims_claim_opened_by_principal", "opened_by_principal_id"),
        Index("ix_claims_claim_sender_principal", "sender_principal_id"),
        Index("ix_claims_claim_status", "status", "submitted_at"),
        # CLM-03 — a decision is only reachable through a review that was recorded.
        CheckConstraint(
            "status NOT IN ('APPROVED', 'REJECTED') OR custody_records_reviewed",
            name="ck_claims_claim_decided_after_custody_review",
        ),
        # CLM-04 — approved means an amount and a decider; rejected means a reason.
        CheckConstraint(
            "status <> 'APPROVED' OR ("
            "compensation_minor_units IS NOT NULL "
            "AND compensation_currency IS NOT NULL "
            "AND decided_at IS NOT NULL AND decided_by_actor_id IS NOT NULL)",
            name="ck_claims_claim_approved_carries_an_amount",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR ("
            "rejection_reason IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_claims_claim_rejected_carries_a_reason",
        ),
        # An amount only ever exists on an approved claim. Before the decision there is
        # no figure, and a stored one would look like a decision nobody made.
        CheckConstraint(
            "compensation_minor_units IS NULL OR status = 'APPROVED'",
            name="ck_claims_claim_amount_only_when_approved",
        ),
        CheckConstraint(
            "(compensation_minor_units IS NULL) = (compensation_currency IS NULL)",
            name="ck_claims_claim_amount_has_currency",
        ),
        CheckConstraint(
            "compensation_minor_units IS NULL OR compensation_minor_units > 0",
            name="ck_claims_claim_amount_is_positive",
        ),
        # v6.3 p.37 — a claim is only ever filed where HUDHUD still carried the risk.
        CheckConstraint(
            "custody_boundary IN ('IN_HUDHUD_CUSTODY', 'OPEN_BOX_AT_THE_DOOR')",
            name="ck_claims_claim_filed_within_hudhud_liability",
        ),
        CheckConstraint("version >= 1", name="ck_claims_claim_version_positive"),
    )

    claim_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_by: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    #: CLM-01 — who is compensated, whoever opened it.
    sender_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    merchant_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    description: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    custody_boundary: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    review_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    custody_records_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    compensation_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    compensation_currency: Mapped[str | None] = mapped_column(String(3))
    rejection_reason: Mapped[str | None] = mapped_column(String(48))
    rejection_note: Mapped[str | None] = mapped_column(Text)
    withdrawn_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ClaimMessageRow(Base):
    """CLM-07 — the support conversation that belongs to one claim."""

    __tablename__ = "claims_messages"
    __table_args__ = (
        Index("ix_claims_message_claim", "claim_id", "written_at"),
        CheckConstraint(
            "author IN ('CLAIMANT', 'SUPPORT')", name="ck_claims_message_author"
        ),
        CheckConstraint(
            "char_length(btrim(body)) > 0", name="ck_claims_message_body_not_blank"
        ),
    )

    message_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    claim_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    author: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    written_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    author_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    attachments_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DriverIncidentRow(Base):
    """DRV-P25 — a driver reporting something about a parcel in their custody.

    **SEC-07: this table has no compensation column and never will.** Driver App v8:
    "No amounts are shown or decided here." An incident that should cost money becomes a
    claim, and `linked_claim_id` points at it; the money lives there.
    """

    __tablename__ = "claims_driver_incidents"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_claims_incident_reference"),
        Index("ix_claims_incident_driver", "reported_by_driver_id", "reported_at"),
        Index(
            "ix_claims_incident_open",
            "status",
            postgresql_where="status <> 'RESOLVED'",
        ),
        # Driver App v8 `needsParcel` — everything but a vehicle or safety issue is
        # about one parcel, and that one is about the driver.
        CheckConstraint(
            "(kind = 'VEHICLE_OR_SAFETY_ISSUE') = (tracking_code IS NULL)",
            name="ck_claims_incident_parcel_matches_kind",
        ),
        # OPS-07 — resolved means operations said what was found.
        CheckConstraint(
            "status <> 'RESOLVED' OR ("
            "resolved_at IS NOT NULL AND resolved_by_actor_id IS NOT NULL "
            "AND char_length(btrim(coalesce(resolution_note, ''))) > 0)",
            name="ck_claims_incident_resolved_is_documented",
        ),
        CheckConstraint(
            "status = 'RESOLVED' OR resolved_at IS NULL",
            name="ck_claims_incident_only_resolved_has_resolved_at",
        ),
    )

    incident_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reported_by_driver_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    reported_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    tracking_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    investigation_started_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    resolved_by_actor_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    linked_claim_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ReferenceSequenceRow(Base):
    """The daily counter behind ``CLM-20260802-000003``.

    Allocated with a single ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` so two
    claimants filing in the same second get different references. Reading a maximum and
    adding one would hand both of them the same one.
    """

    __tablename__ = "claims_reference_sequences"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IntegrationOutboxRow(Base):
    """Claims-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "claims_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_claims_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_claims_outbox_aggregate_version",
        ),
        Index(
            "ix_claims_outbox_pending",
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
    """Claims-owned durable inbox (ADR-0008).

    Uniqueness is `(consumer_name, event_id)`: two consumers in this service each get
    their own chance at the same message, and a redelivery to one of them produces no
    second effect.
    """

    __tablename__ = "claims_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_claims_inbox_consumer_event"
        ),
        Index("ix_claims_inbox_status", "status", "processing_lease_until"),
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
