"""Domain errors."""

from __future__ import annotations


class BridgeDomainError(Exception):
    """Base class for bridge domain failures."""


class SourceTableNotAllowedError(BridgeDomainError):
    """Raised when CDC references a table outside the allowlist."""


class ObservationMappingError(BridgeDomainError):
    """Raised when landing row cannot be mapped to an observation envelope."""


MappingError = ObservationMappingError


class LandingTransactionError(BridgeDomainError):
    """Raised when landing/checkpoint transaction fails."""


class MappingTransactionError(BridgeDomainError):
    """Raised when mapping/outbox transaction fails."""
