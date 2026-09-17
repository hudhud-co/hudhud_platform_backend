"""Identity aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from identity.domain.value_objects import (
    OtpPurpose,
    PrincipalStatus,
    Role,
    ScopeKind,
)


@dataclass(slots=True)
class Principal:
    """One authenticatable person. Holds no profile data — that belongs to `customer`."""

    principal_id: UUID
    phone_hash: str
    phone_last4: str
    status: PrincipalStatus
    created_at: datetime
    status_changed_at: datetime | None = None
    status_reason: str | None = None
    version: int = 1

    @property
    def can_authenticate(self) -> bool:
        return self.status in (
            PrincipalStatus.PENDING_VERIFICATION,
            PrincipalStatus.ACTIVE,
        )


@dataclass(slots=True)
class OtpChallenge:
    """A single-use, time-boxed, attempt-limited code sent to one phone."""

    challenge_id: UUID
    phone_hash: str
    code_hash: str
    purpose: OtpPurpose
    issued_at: datetime
    expires_at: datetime
    max_attempts: int
    failed_attempts: int = 0
    consumed_at: datetime | None = None
    version: int = 1

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def attempts_exhausted(self) -> bool:
        return self.failed_attempts >= self.max_attempts


@dataclass(slots=True)
class Session:
    """An issued bearer session. Revocation is server-side and immediate."""

    session_id: UUID
    principal_id: UUID
    token_hash: str
    device_id_hash: str | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    last_seen_at: datetime | None = None
    version: int = 1

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at


@dataclass(slots=True)
class RoleGrant:
    """A role held by a principal, optionally scoped to one merchant or hub.

    ``scope_id`` is an opaque reference into another context. Identity stores it so a token
    can carry the scope, and never resolves it — that would be cross-context reading.
    """

    grant_id: UUID
    principal_id: UUID
    role: Role
    scope_kind: ScopeKind
    scope_id: UUID | None
    granted_at: datetime
    granted_by: str
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
