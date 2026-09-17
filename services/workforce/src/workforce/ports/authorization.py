"""Authorization boundary for Workforce commands.

The recurring distinction is between the driver a record is about and the support staff
who decide about it. A driver may request an unblock and raise leave; only support or
operations may clear the block or decide the leave (OPS-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class WorkforceCommand(StrEnum):
    APPLICATION_SUBMIT = "application:submit"
    APPLICATION_READ = "application:read"
    APPLICATION_VERIFY = "application:verify"
    APPLICATION_REJECT = "application:reject"

    DRIVER_READ = "driver:read"
    DRIVER_SUSPEND = "driver:suspend"

    SHIFT_PATTERN_READ = "shift_pattern:read"
    SHIFT_PATTERN_UPDATE = "shift_pattern:update"
    SHIFT_START = "shift:start"

    BLOCK_READ = "block:read"
    BLOCK_REQUEST_UNBLOCK = "block:request_unblock"
    BLOCK_CLEAR = "block:clear"

    LEAVE_REQUEST = "leave:request"
    LEAVE_READ = "leave:read"
    LEAVE_DECIDE = "leave:decide"

    ELIGIBILITY_QUERY = "eligibility:query"


class WorkforceRole(StrEnum):
    DRIVER_APPLICANT = "DRIVER_APPLICANT"
    PICKUP_DRIVER = "PICKUP_DRIVER"
    LAST_MILE_DRIVER = "LAST_MILE_DRIVER"
    LINEHAUL_DRIVER = "LINEHAUL_DRIVER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class WorkforceActor:
    principal_id: UUID
    roles: frozenset[WorkforceRole] = field(default_factory=frozenset)

    def has_role(self, role: WorkforceRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        return WorkforceRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return WorkforceRole.SUPPORT in self.roles

    @property
    def may_decide_for_drivers(self) -> bool:
        """OPS-09 — clearing a block and deciding leave are support actions."""
        return self.is_support or self.is_operations

    @property
    def is_driver(self) -> bool:
        return bool(
            self.roles
            & {
                WorkforceRole.PICKUP_DRIVER,
                WorkforceRole.LAST_MILE_DRIVER,
                WorkforceRole.LINEHAUL_DRIVER,
                WorkforceRole.DRIVER_APPLICANT,
            }
        )

    def owns(self, principal_id: UUID) -> bool:
        return self.principal_id == principal_id


@dataclass(frozen=True, slots=True)
class WorkforceAccessDecision:
    outcome: AuthorizationOutcome
    actor: WorkforceActor | None = None

    @staticmethod
    def allow(actor: WorkforceActor) -> WorkforceAccessDecision:
        return WorkforceAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> WorkforceAccessDecision:
        return WorkforceAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> WorkforceAccessDecision:
        return WorkforceAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class WorkforceAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: WorkforceCommand,
        resource_id: UUID | None = None,
    ) -> WorkforceAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
