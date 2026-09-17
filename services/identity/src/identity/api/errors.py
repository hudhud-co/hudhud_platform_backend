"""Domain error to HTTP mapping.

Every authentication failure maps to one status and one code. The mapping is deliberately
lossy: telling a caller that a code expired, that a phone is unknown, or how many attempts
remain would turn this endpoint into an enumeration oracle.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from identity.domain.errors import (
    ConflictingIdempotencyKey,
    DeviceBindingMismatch,
    IdentityError,
    InvalidPhoneNumber,
    OtpRateLimited,
    OtpVerificationFailed,
    PrincipalNotAuthenticable,
    PrincipalNotFound,
    RoleGrantNotPermitted,
    RoleGrantScopeInvalid,
    ServiceCredentialRejected,
    SessionNotActive,
    SessionNotFound,
)

_STATUS: dict[type[IdentityError], int] = {
    InvalidPhoneNumber: 422,
    OtpRateLimited: 429,
    OtpVerificationFailed: 401,
    PrincipalNotFound: 404,
    PrincipalNotAuthenticable: 403,
    SessionNotFound: 404,
    SessionNotActive: 401,
    DeviceBindingMismatch: 401,
    RoleGrantNotPermitted: 409,
    RoleGrantScopeInvalid: 422,
    ServiceCredentialRejected: 403,
    ConflictingIdempotencyKey: 409,
}

_CODE: dict[type[IdentityError], str] = {
    InvalidPhoneNumber: "invalid_phone_number",
    OtpRateLimited: "otp_rate_limited",
    OtpVerificationFailed: "authentication_failed",
    PrincipalNotFound: "principal_not_found",
    PrincipalNotAuthenticable: "principal_not_authenticable",
    SessionNotFound: "session_not_found",
    SessionNotActive: "session_not_active",
    DeviceBindingMismatch: "authentication_failed",
    RoleGrantNotPermitted: "role_grant_not_permitted",
    RoleGrantScopeInvalid: "role_grant_scope_invalid",
    ServiceCredentialRejected: "service_credential_rejected",
    ConflictingIdempotencyKey: "conflicting_idempotency_key",
}

#: Failures whose message must never reach the caller.
_OPAQUE: frozenset[type[IdentityError]] = frozenset(
    {OtpVerificationFailed, DeviceBindingMismatch}
)

_OPAQUE_MESSAGE = "the code could not be verified"


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, IdentityError):
        status = _STATUS.get(type(exc), 400)
        code = _CODE.get(type(exc), "identity_error")
        detail: dict[str, object] = {"code": code}
        if type(exc) in _OPAQUE:
            detail["message"] = _OPAQUE_MESSAGE
        else:
            detail["message"] = str(exc)
        if isinstance(exc, OtpRateLimited):
            detail["retry_after_seconds"] = exc.retry_after_seconds
        raise HTTPException(status_code=status, detail=detail)
    raise exc
