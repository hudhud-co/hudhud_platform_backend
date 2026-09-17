"""Authorization boundary for Claims.

Two rules carry most of the weight.

**SEC-07** — a driver is never shown a compensation or claim value. So there is no
driver capability that returns one, and the driver-facing read model has no field for it.

**CLM-07** — a claimant sees their own claim. "Their own" means the principal who opened
it or the sender it compensates; everything else is support's or operations'.

Operations' capabilities are exactly what CLM-03, CLM-04, OPS-06 and OPS-07 call for and
no more: review a claim, decide it, see the returns-and-claims view, resolve a driver
incident. Nothing here lets Operations act *as* a claimant, file on someone's behalf, or
edit a claimant's words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ClaimsCommand(StrEnum):
    CLAIM_FILE = "claim:file"
    CLAIM_READ_OWN = "claim:read_own"
    CLAIM_WITHDRAW = "claim:withdraw"
    CLAIM_MESSAGE = "claim:message"
    CLAIM_REVIEW = "claim:review"
    CLAIM_DECIDE = "claim:decide"
    CLAIM_QUEUE_READ = "claim:queue_read"
    INCIDENT_REPORT = "incident:report"
    INCIDENT_READ_OWN = "incident:read_own"
    INCIDENT_RESOLVE = "incident:resolve"
    RETURNS_AND_CLAIMS_READ = "returns_and_claims:read"


class ClaimsRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    MERCHANT_OWNER = "MERCHANT_OWNER"
    PICKUP_DRIVER = "PICKUP_DRIVER"
    LAST_MILE_DRIVER = "LAST_MILE_DRIVER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"
    ACCOUNTANT = "ACCOUNTANT"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ClaimsActor:
    principal_id: UUID
    roles: frozenset[ClaimsRole] = field(default_factory=frozenset)
    merchant_id: UUID | None = None

    def has_role(self, role: ClaimsRole) -> bool:
        return role in self.roles

    @property
    def is_driver(self) -> bool:
        return bool(
            self.roles & {ClaimsRole.PICKUP_DRIVER, ClaimsRole.LAST_MILE_DRIVER}
        )

    @property
    def is_support(self) -> bool:
        return ClaimsRole.SUPPORT in self.roles

    @property
    def is_operations(self) -> bool:
        return ClaimsRole.OPERATIONS in self.roles

    @property
    def is_accountant(self) -> bool:
        return ClaimsRole.ACCOUNTANT in self.roles

    @property
    def may_review_a_claim(self) -> bool:
        """CLM-03 — reading the custody record is support's or operations' work."""
        return self.is_support or self.is_operations

    @property
    def may_decide_a_claim(self) -> bool:
        """CLM-04 — approving money is narrower than reviewing.

        Support may review and talk to the claimant; committing HUDHUD to a payment is
        Operations' or an accountant's. Deliberately *not* widened to support.
        """
        return self.is_operations or self.is_accountant

    @property
    def may_resolve_an_incident(self) -> bool:
        """OPS-07 — the driver reports, operations resolves."""
        return self.is_operations

    @property
    def may_see_the_returns_and_claims_view(self) -> bool:
        """OPS-06."""
        return self.is_operations or self.is_support

    @property
    def may_see_a_compensation_value(self) -> bool:
        """SEC-07 — never a driver, whatever else they are.

        Written as an explicit exclusion rather than an allow-list membership test,
        because a driver who is *also* support is still a driver, and the Driver App's
        promise is about what a driver is shown.
        """
        if self.is_driver:
            return False
        return self.is_operations or self.is_accountant or self.is_support


@dataclass(frozen=True, slots=True)
class ClaimsAccessDecision:
    outcome: AuthorizationOutcome
    actor: ClaimsActor | None = None

    @staticmethod
    def allow(actor: ClaimsActor) -> ClaimsAccessDecision:
        return ClaimsAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> ClaimsAccessDecision:
        return ClaimsAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> ClaimsAccessDecision:
        return ClaimsAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class ClaimsAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: ClaimsCommand,
        resource_id: UUID | None = None,
    ) -> ClaimsAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
