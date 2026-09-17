"""Authorization boundary for Merchant commands.

Identity proves who the caller is; this service decides what that principal may touch in
a merchant. Merchant never imports Identity — it asks over HTTP, exactly as Pickup does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class MerchantCommand(StrEnum):
    APPLICATION_CREATE = "application:create"
    APPLICATION_UPDATE = "application:update"
    APPLICATION_SUBMIT = "application:submit"
    APPLICATION_WITHDRAW = "application:withdraw"
    APPLICATION_READ = "application:read"
    APPLICATION_DECIDE = "application:decide"

    MERCHANT_READ = "merchant:read"
    MERCHANT_SUSPEND = "merchant:suspend"
    POLICY_READ = "policy:read"
    POLICY_UPDATE = "policy:update"

    STORE_CREATE = "store:create"
    STORE_UPDATE = "store:update"
    STORE_ARCHIVE = "store:archive"
    STORE_READ = "store:read"

    TEAM_INVITE = "team:invite"
    TEAM_UPDATE = "team:update"
    TEAM_REMOVE = "team:remove"
    TEAM_READ = "team:read"
    TEAM_RESPOND = "team:respond"

    LABEL_STOCK_ISSUE = "label_stock:issue"
    LABEL_STOCK_READ = "label_stock:read"
    PRINTER_AUTHORIZE = "printer:authorize"
    PRINTER_REVOKE = "printer:revoke"

    CATALOGUE_WRITE = "catalogue:write"
    CATALOGUE_READ = "catalogue:read"

    STORE_ACCESS_QUERY = "store_access:query"


class MerchantRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    MERCHANT_OWNER = "MERCHANT_OWNER"
    MERCHANT_MEMBER = "MERCHANT_MEMBER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class MerchantActor:
    """A principal proven by Identity, plus the merchants Identity says they belong to."""

    principal_id: UUID
    roles: frozenset[MerchantRole] = field(default_factory=frozenset)
    merchant_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has_role(self, role: MerchantRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        """Only Operations decides an application — never the applicant."""
        return MerchantRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return MerchantRole.SUPPORT in self.roles

    @property
    def can_review_applications(self) -> bool:
        return self.is_operations


@dataclass(frozen=True, slots=True)
class MerchantAccessDecision:
    outcome: AuthorizationOutcome
    actor: MerchantActor | None = None

    @staticmethod
    def allow(actor: MerchantActor) -> MerchantAccessDecision:
        return MerchantAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> MerchantAccessDecision:
        return MerchantAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> MerchantAccessDecision:
        return MerchantAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class MerchantAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: MerchantCommand,
        resource_id: UUID | None = None,
    ) -> MerchantAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
