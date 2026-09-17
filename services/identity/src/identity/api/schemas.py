"""Request and response models. No request model carries actor identity."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RequestOtpRequest(_Frozen):
    phone: str = Field(min_length=4, max_length=32)
    device_id: str | None = Field(default=None, max_length=128)


class RequestOtpResponse(_Frozen):
    """Deliberately minimal.

    It does not say whether the phone was already registered, and it never carries the
    code — the code exists only in the SMS.
    """

    challenge_id: UUID
    phone_last4: str
    expires_at: datetime


class VerifyOtpRequest(_Frozen):
    challenge_id: UUID
    code: str = Field(min_length=4, max_length=12)
    device_id: str | None = Field(default=None, max_length=128)


class SessionResponse(_Frozen):
    access_token: str
    token_type: str = "Bearer"
    session_id: UUID
    principal_id: UUID
    status: str
    roles: list[str]
    expires_at: datetime


class IntrospectRequest(_Frozen):
    token: str = Field(min_length=1, max_length=512)
    device_id: str | None = Field(default=None, max_length=128)


class GrantResponse(_Frozen):
    role: str
    scope_kind: str
    scope_id: UUID | None = None


class IntrospectResponse(_Frozen):
    active: bool
    principal_id: UUID | None = None
    status: str | None = None
    roles: list[str] = Field(default_factory=list)
    grants: list[GrantResponse] = Field(default_factory=list)
    session_id: UUID | None = None
    expires_at: datetime | None = None


class GrantRoleRequest(_Frozen):
    role: str = Field(min_length=1, max_length=32)
    scope_kind: str = Field(default="GLOBAL", max_length=16)
    scope_id: UUID | None = None


class RoleGrantResponse(_Frozen):
    grant_id: UUID
    principal_id: UUID
    role: str
    scope_kind: str
    scope_id: UUID | None
    granted_at: datetime
    revoked_at: datetime | None = None
    idempotent_replay: bool = False


class PrincipalStatusRequest(_Frozen):
    status: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=256)


class MeResponse(_Frozen):
    principal_id: UUID
    phone_last4: str
    status: str
    roles: list[str]
