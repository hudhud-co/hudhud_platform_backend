"""Classify NATS errors without leaking secret material."""

from __future__ import annotations

AUTHORIZATION_MARKERS = (
    "authorization violation",
    "permissions violation",
    "permission violation",
    "violates permissions",
    "nats: authorization",
    "auth error",
    "user authentication expired",
    "account authentication expired",
)

TLS_MARKERS = (
    "certificate verify failed",
    "ssl",
    "tls",
    "hostname mismatch",
    "certificate",
)

TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "deadline exceeded",
)


def sanitize_error_message(message: str) -> str:
    lowered = message.lower()
    for marker in ("begin ", "seed", "jwt", "creds", "password", "private key"):
        if marker in lowered:
            return "redacted-error"
    return message[:240]


def is_authorization_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = sanitize_error_message(str(current)).lower()
        if any(marker in message for marker in AUTHORIZATION_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_tls_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = sanitize_error_message(str(current)).lower()
        if any(marker in message for marker in TLS_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_timeout_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = sanitize_error_message(str(current)).lower()
        if any(marker in message for marker in TIMEOUT_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def assert_acl_denied(exc: BaseException) -> None:
    """JetStream API denials often surface as timeouts when $JS.API publish is blocked."""
    if is_authorization_error(exc) or is_timeout_error(exc):
        return
    msg = f"expected ACL denial, got {type(exc).__name__}"
    raise AssertionError(msg)
