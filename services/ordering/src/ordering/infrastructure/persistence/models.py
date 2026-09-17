"""SQLAlchemy models for the Ordering-owned database.

Money is stored as an integer column plus an explicit currency column, never as a numeric
with an assumed scale and never as a float (ADR-0012). Invariants that must survive
concurrency — a tracking code being unique, a label being linked once — are database
constraints, not just checks in Python.
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


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_order_reference"),
        Index("ix_order_sender", "sender_principal_id"),
        Index("ix_order_merchant", "sender_merchant_id"),
        CheckConstraint(
            "(sender_kind = 'MERCHANT') = (sender_merchant_id IS NOT NULL)",
            name="ck_order_merchant_sender_has_merchant",
        ),
    )

    order_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sender_merchant_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    sender_store_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ShipmentRequestRow(Base):
    __tablename__ = "shipment_requests"
    __table_args__ = (
        UniqueConstraint("tracking_code", name="uq_shipment_request_tracking_code"),
        # One label, one parcel. A partial index so that a cancelled shipment releases
        # its label rather than retiring a physical sticker for ever.
        Index(
            "uq_shipment_request_label_code",
            "label_code",
            unique=True,
            postgresql_where="label_code IS NOT NULL AND status <> 'CANCELLED'",
        ),
        Index("ix_shipment_request_order", "order_id"),
        Index("ix_shipment_request_sender", "sender_principal_id"),
        Index("ix_shipment_request_merchant", "sender_merchant_id"),
        Index("ix_shipment_request_status", "status"),
        CheckConstraint(
            "(sender_kind = 'MERCHANT') = (sender_merchant_id IS NOT NULL)",
            name="ck_shipment_request_merchant_sender_has_merchant",
        ),
        # v6.3 p.8, Confirmed decision: a regular customer's parcel has no COD option.
        CheckConstraint(
            "payment_terms <> 'CASH_ON_DELIVERY' OR sender_kind = 'MERCHANT'",
            name="ck_shipment_request_no_cod_for_customers",
        ),
        # Money is only ever present as an integer with its currency, or absent entirely.
        CheckConstraint(
            "(cod_amount_minor_units IS NULL) = (cod_currency IS NULL)",
            name="ck_shipment_request_cod_amount_pair",
        ),
        CheckConstraint(
            "cod_amount_minor_units IS NULL OR cod_amount_minor_units >= 0",
            name="ck_shipment_request_cod_not_negative",
        ),
        CheckConstraint(
            "payment_terms <> 'CASH_ON_DELIVERY' OR cod_amount_minor_units IS NOT NULL",
            name="ck_shipment_request_cod_has_amount",
        ),
        # v6.3 p.12: a description is required so the parcel is never an unknown item.
        CheckConstraint("length(btrim(description)) > 0", name="ck_shipment_request_described"),
        CheckConstraint(
            "(receiver_latitude IS NULL) = (receiver_longitude IS NULL)",
            name="ck_shipment_request_geo_pair",
        ),
    )

    request_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    order_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(24), nullable=False)
    sender_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sender_merchant_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    sender_store_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))

    receiver_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    receiver_governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    receiver_name: Mapped[str | None] = mapped_column(String(160))
    receiver_address_line: Mapped[str | None] = mapped_column(String(512))
    receiver_landmark: Mapped[str | None] = mapped_column(String(256))
    receiver_latitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    receiver_longitude: Mapped[object | None] = mapped_column(Numeric(9, 6))

    description: Mapped[str] = mapped_column(String(512), nullable=False)
    goods_category_code: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False)

    weight_grams: Mapped[int | None] = mapped_column(Integer)
    length_cm: Mapped[int | None] = mapped_column(Integer)
    width_cm: Mapped[int | None] = mapped_column(Integer)
    height_cm: Mapped[int | None] = mapped_column(Integer)

    payment_terms: Mapped[str] = mapped_column(String(24), nullable=False)
    cod_amount_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    cod_currency: Mapped[str | None] = mapped_column(String(3))

    open_box_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    photo_documentation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    hudhud_packaging: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    delivery_fee_payer: Mapped[str] = mapped_column(String(16), nullable=False)

    label_code: Mapped[str | None] = mapped_column(String(32))
    label_linked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    pickup_store_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    pickup_window_start: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    pickup_window_end: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    assigned_courier_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))

    delivery_fee_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    packaging_fee_minor_units: Mapped[int | None] = mapped_column(BigInteger)
    fee_currency: Mapped[str | None] = mapped_column(String(3))
    tariff_reference: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(32))
    custody_started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    courier_at_receiver_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class GoodsCategoryRow(Base):
    __tablename__ = "goods_categories"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    hint: Mapped[str | None] = mapped_column(String(256))
    restriction_note: Mapped[str | None] = mapped_column(String(256))
    prohibited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class TariffRateRow(Base):
    __tablename__ = "tariff_rates"
    __table_args__ = (
        Index(
            "ix_tariff_route",
            "origin_governorate",
            "destination_governorate",
            "effective_from",
        ),
        CheckConstraint(
            "delivery_fee_minor_units >= 0 AND packaging_fee_minor_units >= 0",
            name="ck_tariff_fees_not_negative",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_tariff_window_ordered",
        ),
    )

    tariff_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_fee_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    packaging_fee_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IntegrationOutboxRow(Base):
    """Ordering-owned transactional integration outbox (ADR-0008)."""

    __tablename__ = "ordering_integration_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_ordering_outbox_event_id"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            name="uq_ordering_outbox_aggregate_version",
        ),
        Index(
            "ix_ordering_outbox_pending",
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
    """Ordering-owned durable inbox (ADR-0008)."""

    __tablename__ = "ordering_integration_inbox"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name", "event_id", name="uq_ordering_inbox_consumer_event"
        ),
        Index("ix_ordering_inbox_status", "status", "processing_lease_until"),
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
