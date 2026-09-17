"""SQLAlchemy models for the Merchant-owned database.

Every invariant that must hold under concurrency is a database constraint here, not only
a check in the service: two requests racing to set a default pickup point, or to invite
the same number twice, are decided by the index rather than by whoever read first.
"""

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
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MerchantApplicationRow(Base):
    __tablename__ = "merchant_applications"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_merchant_application_reference"),
        # "One application at a time." Enforced as a partial unique index so a terminal
        # application never blocks a later one.
        Index(
            "uq_merchant_application_one_open_per_applicant",
            "applicant_principal_id",
            unique=True,
            postgresql_where="status IN ('DRAFT', 'SUBMITTED', 'CHANGES_REQUESTED')",
        ),
        Index("ix_merchant_application_status", "status"),
    )

    application_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    applicant_principal_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    decided_by_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MerchantRow(Base):
    __tablename__ = "merchants"
    __table_args__ = (
        UniqueConstraint("merchant_code", name="uq_merchant_code"),
        UniqueConstraint("application_id", name="uq_merchant_application"),
        Index("ix_merchant_owner", "owner_principal_id"),
    )

    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    merchant_code: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    application_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    activated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StandingPolicyRow(Base):
    __tablename__ = "merchant_standing_policies"

    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    open_box_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    photo_documentation_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    hudhud_packaging_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    delivery_fee_payer: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StoreRow(Base):
    __tablename__ = "merchant_stores"
    __table_args__ = (
        # At most one live default pickup point per merchant — a second would make a
        # scheduled pickup ambiguous about which branch the courier is going to.
        Index(
            "uq_merchant_store_one_default_pickup",
            "merchant_id",
            unique=True,
            postgresql_where="is_default_pickup AND archived_at IS NULL",
        ),
        Index("ix_merchant_store_merchant", "merchant_id"),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name="ck_merchant_store_geo_pair",
        ),
    )

    store_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    address_line: Mapped[str] = mapped_column(String(512), nullable=False)
    area: Mapped[str | None] = mapped_column(String(160))
    landmark: Mapped[str | None] = mapped_column(String(256))
    latitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    is_default_pickup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TeamMembershipRow(Base):
    __tablename__ = "merchant_team_memberships"
    __table_args__ = (
        # One live membership per number per merchant. Terminal memberships are excluded
        # so a removed member can be invited back.
        Index(
            "uq_merchant_team_live_membership",
            "merchant_id",
            "invited_phone",
            unique=True,
            postgresql_where="status IN ('PENDING', 'ACTIVE')",
        ),
        Index("ix_merchant_team_principal", "member_principal_id"),
        Index("ix_merchant_team_phone", "invited_phone"),
        CheckConstraint(
            "status <> 'ACTIVE' OR member_principal_id IS NOT NULL",
            name="ck_merchant_team_active_has_principal",
        ),
        CheckConstraint(
            "cardinality(store_ids) >= 1", name="ck_merchant_team_has_branch"
        ),
    )

    membership_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    invited_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    store_ids: Mapped[list] = mapped_column(ARRAY(Uuid(as_uuid=True)), nullable=False)
    member_principal_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    invited_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PrinterAuthorizationRow(Base):
    __tablename__ = "merchant_printer_authorizations"
    __table_args__ = (
        Index("ix_merchant_printer_merchant", "merchant_id", "status"),
    )

    authorization_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    printer_serial: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LabelStockRow(Base):
    __tablename__ = "merchant_label_stock"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "batch_reference", name="uq_merchant_label_batch"
        ),
        CheckConstraint("label_count >= 1", name="ck_merchant_label_count_positive"),
        # Consumption can never exceed what was issued, whichever request gets there first.
        CheckConstraint(
            "consumed_count >= 0 AND consumed_count <= label_count",
            name="ck_merchant_label_consumed_within_allocation",
        ),
        # Self-printed stock must name the Hudhud printer it came from (v6.3 p.12).
        CheckConstraint(
            "source <> 'MERCHANT_SELF_PRINTED' OR printer_authorization_id IS NOT NULL",
            name="ck_merchant_label_self_print_authorized",
        ),
        # Packaging seals are a Hudhud-supplied purchase (v6.3 p.14), never self-printed.
        CheckConstraint(
            "stock_kind <> 'PACKAGING_SEAL' OR source = 'HUDHUD_PREPRINTED'",
            name="ck_merchant_seal_stock_is_hudhud_supplied",
        ),
        Index("ix_merchant_label_stock_merchant", "merchant_id"),
    )

    allocation_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    batch_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    label_count: Mapped[int] = mapped_column(Integer, nullable=False)
    consumed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    printer_authorization_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    issued_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProductCategoryRow(Base):
    __tablename__ = "merchant_product_categories"
    __table_args__ = (
        Index(
            "uq_merchant_category_name",
            "merchant_id",
            "name",
            unique=True,
            postgresql_where="archived_at IS NULL",
        ),
    )

    category_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProductRow(Base):
    __tablename__ = "merchant_products"
    __table_args__ = (
        Index("ix_merchant_products_merchant", "merchant_id"),
        Index("ix_merchant_products_category", "category_id"),
    )

    product_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    merchant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationOutboxRow(Base):
    """Merchant-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "merchant_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_merchant_outbox_event_id"),
        # One event per aggregate version: the ordering key consumers rely on.
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_merchant_outbox_aggregate_version",
        ),
        Index(
            "ix_merchant_outbox_pending",
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
    """Merchant-owned durable inbox (ADR-0008).

    Uniqueness is `(consumer_name, event_id)` so two consumers in this service each get
    one chance at the same message, while a redelivery to one consumer gets none.
    """

    __tablename__ = "merchant_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_merchant_inbox_consumer_event"
        ),
        Index("ix_merchant_inbox_status", "status", "processing_lease_until"),
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
