"""Authenticated-actor authorization port for recovery commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pickup.domain.value_objects import RecoveryAction


class RecoveryAuthorizationOutcome(StrEnum):
    """Authorization decision for a recovery command request."""

    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    """Actor identity established by the authorization adapter — never from request body."""

    actor_id: str


@dataclass(frozen=True, slots=True)
class RecoveryAccessDecision:
    """Outcome from authorize_recovery — maps to HTTP status at the adapter."""

    outcome: RecoveryAuthorizationOutcome
    actor: AuthenticatedActor | None = None

    @staticmethod
    def allow(actor: AuthenticatedActor) -> RecoveryAccessDecision:
        return RecoveryAccessDecision(
            outcome=RecoveryAuthorizationOutcome.ALLOWED,
            actor=actor,
        )

    @staticmethod
    def unauthenticated() -> RecoveryAccessDecision:
        return RecoveryAccessDecision(outcome=RecoveryAuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> RecoveryAccessDecision:
        return RecoveryAccessDecision(outcome=RecoveryAuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is RecoveryAuthorizationOutcome.ALLOWED


class AuthorizerUnavailableError(Exception):
    """Authorizer dependency unavailable — HTTP adapter maps to 503."""


class RecoveryAuthorizer(Protocol):
    """Cryptographic authorization boundary — identity headers are never proof."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real authorization adapter is configured (not default-deny)."""

    async def authorize_recovery(
        self,
        *,
        bearer_token: str,
        pickup_task_id: UUID,
        action: RecoveryAction,
    ) -> RecoveryAccessDecision: ...
