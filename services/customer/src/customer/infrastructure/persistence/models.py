"""SQLAlchemy models for the Customer-owned database."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CustomerProfileRow(Base):
    __tablename__ = "customer_profiles"

    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    notification_channels: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LegalAcceptanceRow(Base):
    __tablename__ = "customer_legal_acceptances"
    __table_args__ = (
        # Accepting one version twice is a replay, never a second row.
        UniqueConstraint(
            "principal_id",
            "kind",
            "document_version",
            name="uq_customer_legal_once_per_version",
        ),
    )

    acceptance_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactRow(Base):
    __tablename__ = "customer_contacts"
    __table_args__ = (Index("ix_customer_contacts_owner", "owner_principal_id"),)

    contact_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AddressRow(Base):
    __tablename__ = "customer_addresses"
    __table_args__ = (
        Index("ix_customer_addresses_owner_kind", "owner_principal_id", "kind"),
    )

    address_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    governorate: Mapped[str] = mapped_column(String(32), nullable=False)
    line: Mapped[str] = mapped_column(String(512), nullable=False)
    contact_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    landmark: Mapped[str | None] = mapped_column(String(256))
    latitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[object | None] = mapped_column(Numeric(9, 6))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
