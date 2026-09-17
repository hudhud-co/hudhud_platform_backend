"""Authorization boundary for Ordering commands.

Identity proves who the caller is. Merchant says what a principal may do inside a
merchant. Ordering combines the two and never imports either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class OrderingCommand(StrEnum):
    ORDER_CREATE = "order:create"
    ORDER_READ = "order:read"
    ORDER_CANCEL = "order:cancel"
    SHIPMENT_CREATE = "shipment:create"
    SHIPMENT_READ = "shipment:read"
    SHIPMENT_EDIT = "shipment:edit"
    SHIPMENT_CANCEL = "shipment:cancel"
    LABEL_LINK = "label:link"
    PICKUP_BOOK = "pickup:book"
    QUOTE_READ = "quote:read"
    CATALOGUE_READ = "catalogue:read"
    TARIFF_WRITE = "tariff:write"
    SUPPORT_CORRECTION = "support:correction"


class OrderingRole(StrEnum):
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
class OrderingActor:
    principal_id: UUID
    roles: frozenset[OrderingRole] = field(default_factory=frozenset)
    merchant_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has_role(self, role: OrderingRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        return OrderingRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return OrderingRole.SUPPORT in self.roles

    @property
    def can_correct_in_custody(self) -> bool:
        """"Support applies it before the delivery attempt" — a sender cannot."""
        return self.is_support or self.is_operations

    def acts_for_merchant(self, merchant_id: UUID) -> bool:
        return merchant_id in self.merchant_ids


@dataclass(frozen=True, slots=True)
class OrderingAccessDecision:
    outcome: AuthorizationOutcome
    actor: OrderingActor | None = None

    @staticmethod
    def allow(actor: OrderingActor) -> OrderingAccessDecision:
        return OrderingAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> OrderingAccessDecision:
        return OrderingAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> OrderingAccessDecision:
        return OrderingAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class OrderingAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: OrderingCommand,
        resource_id: UUID | None = None,
    ) -> OrderingAccessDecision: ...


@dataclass(frozen=True, slots=True)
class StoreAccess:
    """A team member's reach inside one merchant, as Merchant reports it."""

    merchant_id: UUID
    role: str
    permissions: frozenset[str]
    store_ids: tuple[UUID, ...]

    @property
    def may_author_shipments(self) -> bool:
        """Always false today, and stated as a rule rather than as an omission.

        Customer App v3: a store team member "Cannot create, edit or cancel a shipment".
        Ordering checks this itself rather than assuming Merchant will never grant it.
        """
        return "shipment:create" in self.permissions

    @property
    def may_read_store_parcels(self) -> bool:
        """Customer App v3: a keeper "Sees the store's parcels and their details".

        This is the read half of the same rule as :attr:`may_author_shipments` — the
        capability string is Merchant's `store_parcel:read`, checked explicitly so a
        future role cannot pick up store reads merely by existing.
        """
        return "store_parcel:read" in self.permissions


class MerchantAccessPort(Protocol):
    """What Ordering needs to know from Merchant, asked over HTTP."""

    @property
    def is_production_ready(self) -> bool: ...

    async def store_access(self, principal_id: UUID) -> tuple[StoreAccess, ...]: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
