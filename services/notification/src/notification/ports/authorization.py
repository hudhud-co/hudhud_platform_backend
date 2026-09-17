"""Authorization boundary for Notification commands.

Almost everything here is about one person's own data: their preferences and their own
notification centre. The only wider reach is Operations sending a bulletin to drivers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class NotificationCommand(StrEnum):
    PREFERENCES_READ = "preferences:read"
    PREFERENCES_UPDATE = "preferences:update"
    CENTRE_READ = "centre:read"
    CENTRE_MARK_READ = "centre:mark_read"
    BULLETIN_SEND = "bulletin:send"
    DELIVERY_READ = "delivery:read"


class NotificationRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    MERCHANT_OWNER = "MERCHANT_OWNER"
    MERCHANT_MEMBER = "MERCHANT_MEMBER"
    DRIVER = "DRIVER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class NotificationActor:
    principal_id: UUID
    roles: frozenset[NotificationRole] = field(default_factory=frozenset)

    def has_role(self, role: NotificationRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        return NotificationRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return NotificationRole.SUPPORT in self.roles

    def owns(self, principal_id: UUID) -> bool:
        return self.principal_id == principal_id


@dataclass(frozen=True, slots=True)
class NotificationAccessDecision:
    outcome: AuthorizationOutcome
    actor: NotificationActor | None = None

    @staticmethod
    def allow(actor: NotificationActor) -> NotificationAccessDecision:
        return NotificationAccessDecision(
            outcome=AuthorizationOutcome.ALLOWED, actor=actor
        )

    @staticmethod
    def unauthenticated() -> NotificationAccessDecision:
        return NotificationAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> NotificationAccessDecision:
        return NotificationAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class NotificationAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: NotificationCommand,
        resource_id: UUID | None = None,
    ) -> NotificationAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
