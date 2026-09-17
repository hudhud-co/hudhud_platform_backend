"""Authorization boundary for Hub commands.

The distinction that matters here is not merchant versus customer but *staff versus
everyone else*: v6.3 p.18 makes labelling a hub-staff action specifically, and every
custody-changing action must be attributable to a proven actor (SEC-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class HubCommand(StrEnum):
    DROP_OFF_REGISTER = "drop_off:register"
    DROP_OFF_CAPTURE_DETAILS = "drop_off:capture_details"
    DROP_OFF_LABEL = "drop_off:label"
    DROP_OFF_ACCEPT = "drop_off:accept"
    DROP_OFF_READ = "drop_off:read"

    PARCEL_SCAN_IN = "parcel:scan_in"
    PARCEL_SORT = "parcel:sort"
    PARCEL_HOLD = "parcel:hold"
    PARCEL_HANDOVER = "parcel:handover"
    PARCEL_READ = "parcel:read"

    CONSIGNMENT_CREATE = "consignment:create"
    CONSIGNMENT_SEAL = "consignment:seal"
    CONSIGNMENT_DISPATCH = "consignment:dispatch"
    CONSIGNMENT_RECEIVE = "consignment:receive"
    CONSIGNMENT_READ = "consignment:read"

    LINEHAUL_PLAN = "linehaul:plan"
    LINEHAUL_REPORT = "linehaul:report"
    LINEHAUL_READ = "linehaul:read"

    HUB_ADMIN = "hub:admin"
    ACTIVITY_READ = "activity:read"


class HubRole(StrEnum):
    HUB_OPERATOR = "HUB_OPERATOR"
    LINEHAUL_DRIVER = "LINEHAUL_DRIVER"
    LAST_MILE_DRIVER = "LAST_MILE_DRIVER"
    SUPPORT = "SUPPORT"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class HubActor:
    principal_id: UUID
    roles: frozenset[HubRole] = field(default_factory=frozenset)
    #: Which facilities this principal is posted to. An operator at one hub is not
    #: automatically an operator at another.
    hub_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has_role(self, role: HubRole) -> bool:
        return role in self.roles

    @property
    def is_operations(self) -> bool:
        return HubRole.OPERATIONS in self.roles

    @property
    def is_support(self) -> bool:
        return HubRole.SUPPORT in self.roles

    @property
    def is_hub_staff(self) -> bool:
        return HubRole.HUB_OPERATOR in self.roles

    @property
    def is_linehaul_driver(self) -> bool:
        return HubRole.LINEHAUL_DRIVER in self.roles

    def posted_to(self, hub_id: UUID) -> bool:
        return self.is_operations or hub_id in self.hub_ids


@dataclass(frozen=True, slots=True)
class HubAccessDecision:
    outcome: AuthorizationOutcome
    actor: HubActor | None = None

    @staticmethod
    def allow(actor: HubActor) -> HubAccessDecision:
        return HubAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> HubAccessDecision:
        return HubAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> HubAccessDecision:
        return HubAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class HubAuthorizer(Protocol):
    @property
    def is_production_ready(self) -> bool: ...

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: HubCommand,
        resource_id: UUID | None = None,
    ) -> HubAccessDecision: ...


class AuthorizerUnavailableError(RuntimeError):
    """The authorization boundary could not be reached — never a user denial."""
