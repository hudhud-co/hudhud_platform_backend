"""Authorization boundary for Delivery commands.

Almost every command here is a driver acting on a parcel in their own custody, so the
recurring check is not a role but an identity: the actor must be the driver the stop was
assigned to. Operations decides what happens after a failed attempt (OPS-08), and a
customer acts only on their own parcel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class DeliveryCommand(StrEnum):
    MANIFEST_BUILD = "manifest:build"
    MANIFEST_READ = "manifest:read"

    STOP_DEPART = "stop:depart"
    STOP_ARRIVE = "stop:arrive"
    STOP_WAIT = "stop:wait"
    STOP_VERIFY = "stop:verify"
    STOP_SEAL_CHECK = "stop:seal_check"
    STOP_INSPECT = "stop:inspect"
    STOP_PHOTO = "stop:photo"
    STOP_PAY = "stop:pay"
    STOP_COMPLETE = "stop:complete"
    STOP_FAIL = "stop:fail"
    STOP_READ = "stop:read"

    NEXT_ATTEMPT_DECIDE = "next_attempt:decide"

    RECEIVER_PREFERENCE_SET = "receiver_preference:set"
    RECEIVER_ISSUE_REPORT = "receiver_issue:report"
    COURIER_RATE = "courier:rate"
    COURIER_RATING_READ = "courier_rating:read"


class DeliveryRole(StrEnum):
    LAST_MILE_DRIVER = "LAST_MILE_DRIVER"
    CUSTOMER = "CUSTOMER"
    MERCHANT_OWNER = "MERCHANT_OWNER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class DeliveryActor:
    principal_id: UUID
    roles: frozenset[DeliveryRole] = field(default_factory=frozenset)

    def has_role(self, role: DeliveryRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        return DeliveryRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return DeliveryRole.SUPPORT in self.roles

    @property
    def is_driver(self) -> bool:
        return DeliveryRole.LAST_MILE_DRIVER in self.roles

    @property
    def may_decide_next_attempt(self) -> bool:
        """OPS-08 — the decision is operations', never the driver's."""
        return self.is_operations

    def owns(self, principal_id: UUID) -> bool:
        return self.principal_id == principal_id


@dataclass(frozen=True, slots=True)
class DeliveryAccessDecision:
    outcome: AuthorizationOutcome
    actor: DeliveryActor | None = None

    @staticmethod
    def allow(actor: DeliveryActor) -> DeliveryAccessDecision:
        return DeliveryAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> DeliveryAccessDecision:
        return DeliveryAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> DeliveryAccessDecision:
        return DeliveryAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class DeliveryAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: DeliveryCommand,
        resource_id: UUID | None = None,
    ) -> DeliveryAccessDecision: ...


@dataclass(frozen=True, slots=True)
class DriverEligibility:
    """What Workforce says about whether this driver may be given work."""

    eligible: bool
    reasons: tuple[str, ...] = ()


class WorkforceEligibilityPort(Protocol):
    """Delivery asks Workforce before putting a parcel in a driver's custody (ADR-0013)."""

    @property
    def is_production_ready(self) -> bool: ...

    async def eligibility_for(self, principal_id: UUID) -> DriverEligibility: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
