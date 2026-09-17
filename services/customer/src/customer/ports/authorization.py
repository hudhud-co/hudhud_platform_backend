"""Authorization boundary for Customer commands.

Identity proves who the caller is; this service decides what that principal may touch.
Ownership is the whole model here: a customer may read and write their own profile,
contacts and addresses, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class CustomerCommand(StrEnum):
    PROFILE_READ = "profile:read"
    PROFILE_UPDATE = "profile:update"
    LEGAL_ACCEPT = "legal:accept"
    LEGAL_READ = "legal:read"
    CONTACT_CREATE = "contact:create"
    CONTACT_UPDATE = "contact:update"
    CONTACT_ARCHIVE = "contact:archive"
    CONTACT_READ = "contact:read"
    ADDRESS_CREATE = "address:create"
    ADDRESS_UPDATE = "address:update"
    ADDRESS_ARCHIVE = "address:archive"
    ADDRESS_RESTORE = "address:restore"
    ADDRESS_SET_DEFAULT = "address:set_default"
    ADDRESS_READ = "address:read"


class CustomerRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    MERCHANT_MEMBER = "MERCHANT_MEMBER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class CustomerActor:
    principal_id: UUID
    roles: frozenset[CustomerRole] = field(default_factory=frozenset)

    def has_role(self, role: CustomerRole) -> bool:
        return role in self.roles

    def owns(self, principal_id: UUID) -> bool:
        return self.principal_id == principal_id

    @property
    def can_act_for_others(self) -> bool:
        """Support and Operations may read another customer to resolve a ticket."""
        return bool(self.roles & {CustomerRole.SUPPORT, CustomerRole.OPERATIONS})


@dataclass(frozen=True, slots=True)
class CustomerAccessDecision:
    outcome: AuthorizationOutcome
    actor: CustomerActor | None = None

    @staticmethod
    def allow(actor: CustomerActor) -> CustomerAccessDecision:
        return CustomerAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> CustomerAccessDecision:
        return CustomerAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> CustomerAccessDecision:
        return CustomerAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class CustomerAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: CustomerCommand,
        resource_id: UUID | None = None,
    ) -> CustomerAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
