"""Authenticated actor / authorization port for acceptance commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AcceptanceAuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    """Cryptographically established actor — never taken from identity headers."""

    user_id: str


@dataclass(frozen=True, slots=True)
class AcceptanceAccessDecision:
    outcome: AcceptanceAuthorizationOutcome
    actor: AuthenticatedActor | None = None

    @staticmethod
    def allow(actor: AuthenticatedActor) -> AcceptanceAccessDecision:
        return AcceptanceAccessDecision(
            outcome=AcceptanceAuthorizationOutcome.ALLOWED,
            actor=actor,
        )

    @staticmethod
    def unauthenticated() -> AcceptanceAccessDecision:
        return AcceptanceAccessDecision(outcome=AcceptanceAuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> AcceptanceAccessDecision:
        return AcceptanceAccessDecision(outcome=AcceptanceAuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AcceptanceAuthorizationOutcome.ALLOWED


class AuthorizerUnavailableError(Exception):
    """Authorizer dependency unavailable — HTTP adapter maps to 503."""


class AcceptanceAuthorizer(Protocol):
    """Authorization boundary — X-User-Id / X-Role / body / query are never proof."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real authorization adapter is configured (not default-deny)."""

    async def authorize_acceptance_scan(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
        pickup_task_id: UUID,
    ) -> AcceptanceAccessDecision: ...
