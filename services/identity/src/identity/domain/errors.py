"""Domain errors for the Identity service."""

from __future__ import annotations


class IdentityError(Exception):
    """Base Identity domain error."""


class InvalidPhoneNumber(IdentityError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"invalid phone number: {reason}")


class OtpRateLimited(IdentityError):
    """Too many codes requested for one phone inside the window."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"otp rate limited; retry after {retry_after_seconds}s")


class OtpVerificationFailed(IdentityError):
    """A code did not authenticate. The reason is for logs, not for the caller."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"otp verification failed: {reason}")


class PrincipalNotFound(IdentityError):
    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        super().__init__(f"principal not found: {principal_id}")


class PrincipalNotAuthenticable(IdentityError):
    def __init__(self, *, principal_id: str, status: str) -> None:
        self.principal_id = principal_id
        self.status = status
        super().__init__(f"principal {principal_id} cannot authenticate (status={status})")


class SessionNotFound(IdentityError):
    def __init__(self) -> None:
        super().__init__("session not found")


class SessionNotActive(IdentityError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"session not active: {reason}")


class DeviceBindingMismatch(IdentityError):
    """A session token presented from a device it was not issued to."""

    def __init__(self) -> None:
        super().__init__("session is bound to a different device")


class RoleGrantNotPermitted(IdentityError):
    def __init__(self, *, role: str, reason: str) -> None:
        self.role = role
        self.reason = reason
        super().__init__(f"role {role} cannot be granted: {reason}")


class RoleGrantScopeInvalid(IdentityError):
    def __init__(self, *, role: str, scope_kind: str) -> None:
        self.role = role
        self.scope_kind = scope_kind
        super().__init__(f"role {role} is not valid for scope {scope_kind}")


class ServiceCredentialRejected(IdentityError):
    """Token introspection is service-to-service; a user token may not call it."""

    def __init__(self) -> None:
        super().__init__("service credential rejected")


class ConflictingIdempotencyKey(IdentityError):
    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"idempotency key reused with different content: {idempotency_key}")
