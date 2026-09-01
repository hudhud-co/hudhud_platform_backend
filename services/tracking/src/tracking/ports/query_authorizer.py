"""Service-owned authorization port for timeline query reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class TrackingAuthorizationOutcome(StrEnum):
    """Authorization decision for a timeline read request."""

    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class TrackingAccessDecision:
    """Outcome from authorize_timeline_read — maps to HTTP status at the adapter."""

    outcome: TrackingAuthorizationOutcome

    @staticmethod
    def allow() -> TrackingAccessDecision:
        return TrackingAccessDecision(outcome=TrackingAuthorizationOutcome.ALLOWED)

    @staticmethod
    def unauthenticated() -> TrackingAccessDecision:
        return TrackingAccessDecision(outcome=TrackingAuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> TrackingAccessDecision:
        return TrackingAccessDecision(outcome=TrackingAuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is TrackingAuthorizationOutcome.ALLOWED


class AuthorizerUnavailableError(Exception):
    """Authorizer dependency unavailable — HTTP adapter maps to 503."""


class TrackingQueryAuthorizer(Protocol):
    """Cryptographic authorization boundary — identity headers are never proof."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real authorization adapter is configured (not default-deny)."""

    async def authorize_timeline_read(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
    ) -> TrackingAccessDecision: ...
