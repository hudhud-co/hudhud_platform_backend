"""W20-A: Identity core — principals, OTP challenges, sessions, role grants.

No column stores a phone number, code, token or device id in recoverable form; each is a
keyed hash. ``phone_last4`` is the only displayable fragment.

A partial unique index enforces the rule that a principal may hold one live grant of a
given role at a given scope — re-granting is an idempotent replay, not a duplicate row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Keep <=32 chars — Alembic default version_num is VARCHAR(32).
revision: str = "w20a_identity_core_001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_principals",
        sa.Column("principal_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_last4", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.String(length=256), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("phone_hash", name="uq_identity_principals_phone_hash"),
    )

    op.create_table(
        "identity_otp_challenges",
        sa.Column("challenge_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_identity_otp_phone_issued",
        "identity_otp_challenges",
        ["phone_hash", "issued_at"],
    )
    # At most one live code per phone: requesting a new one invalidates the previous.
    op.create_index(
        "uq_identity_otp_one_open_per_phone",
        "identity_otp_challenges",
        ["phone_hash"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )

    op.create_table(
        "identity_sessions",
        sa.Column("session_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_id_hash", sa.String(length=64), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=128), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("token_hash", name="uq_identity_sessions_token_hash"),
    )
    op.create_index("ix_identity_sessions_principal", "identity_sessions", ["principal_id"])

    op.create_table(
        "identity_role_grants",
        sa.Column("grant_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("principal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", sa.String(length=128), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_identity_role_grants_principal", "identity_role_grants", ["principal_id"]
    )
    # One live grant per (principal, role, scope). Two rows would make revocation
    # ambiguous — revoking one would leave the principal still holding the role.
    op.create_index(
        "uq_identity_role_grant_live_scoped",
        "identity_role_grants",
        ["principal_id", "role", "scope_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND scope_id IS NOT NULL"),
    )
    op.create_index(
        "uq_identity_role_grant_live_global",
        "identity_role_grants",
        ["principal_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND scope_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_identity_role_grant_live_global", table_name="identity_role_grants")
    op.drop_index("uq_identity_role_grant_live_scoped", table_name="identity_role_grants")
    op.drop_index("ix_identity_role_grants_principal", table_name="identity_role_grants")
    op.drop_table("identity_role_grants")
    op.drop_index("ix_identity_sessions_principal", table_name="identity_sessions")
    op.drop_table("identity_sessions")
    op.drop_index("uq_identity_otp_one_open_per_phone", table_name="identity_otp_challenges")
    op.drop_index("ix_identity_otp_phone_issued", table_name="identity_otp_challenges")
    op.drop_table("identity_otp_challenges")
    op.drop_table("identity_principals")
