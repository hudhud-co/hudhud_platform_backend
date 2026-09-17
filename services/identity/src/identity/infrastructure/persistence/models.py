"""SQLAlchemy models for the Identity-owned database.

No column in this schema stores a phone number, an OTP code, a session token or a device
identifier in recoverable form. Every one of them is a keyed hash, except ``phone_last4``,
which is the only fragment safe to display.
"""

from __future__ import annotations

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PrincipalRow(Base):
    __tablename__ = "identity_principals"
    __table_args__ = (
        UniqueConstraint("phone_hash", name="uq_identity_principals_phone_hash"),
    )

    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_last4: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    status_changed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OtpChallengeRow(Base):
    __tablename__ = "identity_otp_challenges"
    __table_args__ = (
        # Rate limiting counts challenges per phone inside a time window.
        Index("ix_identity_otp_phone_issued", "phone_hash", "issued_at"),
    )

    challenge_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SessionRow(Base):
    __tablename__ = "identity_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_identity_sessions_token_hash"),
        Index("ix_identity_sessions_principal", "principal_id"),
    )

    session_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id_hash: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(128))
    last_seen_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RoleGrantRow(Base):
    __tablename__ = "identity_role_grants"
    __table_args__ = (
        Index("ix_identity_role_grants_principal", "principal_id"),
    )

    grant_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    principal_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    granted_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(128))
