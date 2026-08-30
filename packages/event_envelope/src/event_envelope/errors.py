"""Envelope errors with redacted representations."""

from __future__ import annotations

from typing import Any


class EnvelopeError(Exception):
    """Base class for envelope errors."""


class EnvelopeValidationError(EnvelopeError):
    """Raised when envelope validation fails.

    ``field`` identifies the failing field. ``detail`` is a safe, non-sensitive summary.
    """

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"{field}: {detail}")

    def __repr__(self) -> str:
        return f"EnvelopeValidationError(field={self.field!r}, detail={self.detail!r})"


class UnsupportedEnvelopeVersionError(EnvelopeError):
    """Raised when ``envelope_version`` is outside the supported compatibility window."""

    def __init__(self, envelope_version: int, supported_version: int) -> None:
        self.envelope_version = envelope_version
        self.supported_version = supported_version
        super().__init__(
            f"envelope_version {envelope_version} is not supported "
            f"(supported: {supported_version})"
        )

    def __repr__(self) -> str:
        return (
            f"UnsupportedEnvelopeVersionError("
            f"envelope_version={self.envelope_version!r}, "
            f"supported_version={self.supported_version!r})"
        )


def safe_error_context(*, field: str, reason: str) -> dict[str, Any]:
    """Build a log-safe error context without payload or metadata values."""
    return {"field": field, "reason": reason}
