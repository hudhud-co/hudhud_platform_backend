"""Domain errors with sanitized representations."""

from __future__ import annotations


class TrackingError(Exception):
    """Base Tracking service error."""


class ContractRejection(TrackingError):
    """Permanent A1 contract mismatch — quarantine, do not project."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")

    def __repr__(self) -> str:
        return f"ContractRejection(code={self.code!r}, detail={self.detail!r})"


class RetryableHandlerError(TrackingError):
    """Retryable failure before commit — rollback and NAK."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class PoisonHandlerError(TrackingError):
    """Permanent handler failure — quarantine per terminal policy."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")
