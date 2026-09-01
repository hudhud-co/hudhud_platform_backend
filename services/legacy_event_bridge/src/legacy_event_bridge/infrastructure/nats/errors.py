"""Sanitized NATS publish errors — no secrets or payload in messages."""

from __future__ import annotations

from legacy_event_bridge.domain.sanitize import sanitize_error_message


class NatsPublishError(Exception):
    """Base publish failure with a messaging_conformance error code."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = sanitize_error_message(message)
        super().__init__(self.message)


class PayloadTooLargeError(NatsPublishError):
    def __init__(self, message: str = "envelope exceeds transport size limit") -> None:
        super().__init__("PAYLOAD_TOO_LARGE", message)


class SubjectForbiddenError(NatsPublishError):
    def __init__(self, message: str = "subject not allowlisted") -> None:
        super().__init__("SUBJECT_FORBIDDEN", message)


class StreamMismatchError(NatsPublishError):
    def __init__(self, message: str = "jetstream ack stream mismatch") -> None:
        super().__init__("SUBJECT_FORBIDDEN", message)


class NatsTimeoutError(NatsPublishError):
    def __init__(self, message: str = "jetstream publish timed out") -> None:
        super().__init__("NATS_TIMEOUT", message)


class NatsTemporaryError(NatsPublishError):
    def __init__(self, message: str = "jetstream publish failed") -> None:
        super().__init__("NATS_TEMPORARY", message)


class NatsAclDeniedError(NatsPublishError):
    def __init__(self, message: str = "jetstream publish denied") -> None:
        super().__init__("ACL_DENIED", message)


class NatsNotConfiguredError(RuntimeError):
    """Raised when NATS is requested without explicit configuration."""
