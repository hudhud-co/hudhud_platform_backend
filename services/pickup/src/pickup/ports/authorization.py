"""Authorization boundary for Pickup driver, sender, and hub commands.

Identity is established cryptographically by the adapter from the presented bearer
token. Request bodies and forwarded identity headers are never proof of identity,
role, merchant membership, or hub scope (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class PickupCommand(StrEnum):
    """Authorizable Pickup operations, grouped by the authority they require."""

    # Driver workforce
    WORK_SESSION_START = "work_session:start"
    WORK_SESSION_PAUSE = "work_session:pause"
    WORK_SESSION_RESUME = "work_session:resume"
    WORK_SESSION_END = "work_session:end"
    WORK_SESSION_READ = "work_session:read"

    # Driver task lifecycle
    TASK_ACKNOWLEDGE = "pickup_task:acknowledge"
    TASK_DECLINE = "pickup_task:decline"
    TASK_ARRIVE = "pickup_task:arrive"
    TASK_SCAN = "pickup_task:scan"
    TASK_CAPTURE_PROOF = "pickup_task:capture_proof"
    TASK_REPORT_EXCEPTION = "pickup_task:report_exception"
    TASK_FAIL = "pickup_task:fail"
    TASK_ACCEPT = "pickup_task:accept"
    TASK_REFUSE = "pickup_task:refuse"
    TASK_NOT_PRESENTED = "pickup_task:not_presented"
    TASK_READ = "pickup_task:read"
    TASK_READ_ACCEPTANCE = "pickup_task:read_acceptance"

    # Merchant stop
    STOP_RESOLVE_SCAN = "pickup_stop:resolve_scan"
    STOP_READ = "pickup_stop:read"

    # Sender handover ceremony
    CHALLENGE_ISSUE = "courier_challenge:issue"
    CHALLENGE_VERIFY = "courier_challenge:verify"
    MANIFEST_SUBMIT = "courier_manifest:submit"
    MANIFEST_CONFIRM = "courier_manifest:confirm"

    # Driver-to-hub handover
    HANDOVER_CREATE = "handover_manifest:create"
    HANDOVER_ARRIVE = "handover_manifest:arrive"
    HANDOVER_CANCEL = "handover_manifest:cancel"
    HANDOVER_RECEIVE = "handover_manifest:receive"
    HANDOVER_CLOSE = "handover_manifest:close"
    HANDOVER_READ = "handover_manifest:read"

    # Offline work
    OFFLINE_AUTHORIZE = "offline_authorization:issue"
    OFFLINE_REVOKE = "offline_authorization:revoke"
    OFFLINE_SYNC = "offline_sync:submit"
    OFFLINE_READ = "offline_sync:read"

    # Operations
    RECONCILIATION_READ = "reconciliation_case:read"
    RECONCILIATION_RESOLVE = "reconciliation_case:resolve"


class PickupRole(StrEnum):
    """Role classes Pickup distinguishes. The identity context owns role assignment."""

    PICKUP_DRIVER = "PICKUP_DRIVER"
    MERCHANT_MEMBER = "MERCHANT_MEMBER"
    CUSTOMER_SENDER = "CUSTOMER_SENDER"
    HUB_OPERATOR = "HUB_OPERATOR"
    OPERATIONS = "OPERATIONS"


class AuthorizationOutcome(StrEnum):
    ALLOWED = "allowed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class PickupActor:
    """Actor identity and scopes proven by the authorization adapter."""

    actor_id: str
    roles: frozenset[PickupRole] = field(default_factory=frozenset)
    hub_ids: frozenset[UUID] = field(default_factory=frozenset)
    merchant_ids: frozenset[UUID] = field(default_factory=frozenset)

    def has_role(self, role: PickupRole) -> bool:
        return role in self.roles

    def scoped_to_hub(self, hub_id: UUID) -> bool:
        return hub_id in self.hub_ids

    @property
    def primary_role(self) -> str:
        for role in (
            PickupRole.OPERATIONS,
            PickupRole.HUB_OPERATOR,
            PickupRole.PICKUP_DRIVER,
            PickupRole.MERCHANT_MEMBER,
            PickupRole.CUSTOMER_SENDER,
        ):
            if role in self.roles:
                return role.value
        return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PickupAccessDecision:
    outcome: AuthorizationOutcome
    actor: PickupActor | None = None

    @staticmethod
    def allow(actor: PickupActor) -> PickupAccessDecision:
        return PickupAccessDecision(outcome=AuthorizationOutcome.ALLOWED, actor=actor)

    @staticmethod
    def unauthenticated() -> PickupAccessDecision:
        return PickupAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)

    @staticmethod
    def forbidden() -> PickupAccessDecision:
        return PickupAccessDecision(outcome=AuthorizationOutcome.FORBIDDEN)

    @property
    def allowed(self) -> bool:
        return self.outcome is AuthorizationOutcome.ALLOWED


class PickupAuthorizer(Protocol):
    """Cryptographic authorization boundary — identity headers are never proof."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real authorization adapter is configured (not default-deny)."""

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: PickupCommand,
        resource_id: UUID | None = None,
    ) -> PickupAccessDecision: ...
