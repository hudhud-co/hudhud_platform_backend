"""Domain errors with sanitized representations."""

from __future__ import annotations


class AuditError(Exception):
    """Base Audit service error."""


class ContractRejection(AuditError):
    """Permanent A2 contract mismatch — quarantine, do not project."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")

    def __repr__(self) -> str:
        return f"ContractRejection(code={self.code!r}, detail={self.detail!r})"


class RetryableHandlerError(AuditError):
    """Retryable failure before commit — rollback and NAK."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class PoisonHandlerError(AuditError):
    """Permanent handler failure — quarantine per terminal policy."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")
