"""Production Pickup authorizer backed by the Identity service.

Pickup never imports Identity. It speaks the versioned HTTP introspection contract and
maps the answer onto its own ``PickupActor``. Everything this adapter cannot prove is a
denial: an unreachable Identity, a malformed answer, an unknown role or an unparsable
scope all fail closed rather than degrading into a partial grant.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pickup.ports.authorization import (
    PickupAccessDecision,
    PickupActor,
    PickupCommand,
    PickupRole,
)
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError

#: Identity role name -> the role Pickup understands. Roles Pickup has no concept of are
#: dropped deliberately: an unmapped role must never widen a Pickup actor's authority.
_ROLE_MAP: dict[str, PickupRole] = {
    "PICKUP_DRIVER": PickupRole.PICKUP_DRIVER,
    "MERCHANT_MEMBER": PickupRole.MERCHANT_MEMBER,
    "CUSTOMER": PickupRole.CUSTOMER_SENDER,
    "HUB_OPERATOR": PickupRole.HUB_OPERATOR,
    "OPERATIONS": PickupRole.OPERATIONS,
}

#: Principal statuses Identity may report that Pickup still refuses to act on.
_REFUSED_STATUSES = frozenset({"SUSPENDED", "CLOSED"})


class IntrospectionTransport(Protocol):
    """Minimal transport so the adapter is testable without a live socket."""

    async def introspect(self, *, token: str) -> dict[str, object]:
        """Return the decoded introspection body, or raise to signal unavailability."""


class IdentityIntrospectionAuthorizer:
    """Resolve a bearer token into a Pickup actor through Identity."""

    def __init__(self, transport: IntrospectionTransport) -> None:
        self._transport = transport

    @property
    def is_production_ready(self) -> bool:
        return True

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: PickupCommand,
        resource_id: UUID | None = None,
    ) -> PickupAccessDecision:
        _ = (command, resource_id)
        if not bearer_token.strip():
            return PickupAccessDecision.unauthenticated()

        try:
            body = await self._transport.introspect(token=bearer_token)
        except AuthorizerUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport fault is unavailability
            raise AuthorizerUnavailableError("identity introspection failed") from exc

        actor = build_actor(body)
        if actor is None:
            return PickupAccessDecision.unauthenticated()
        return PickupAccessDecision.allow(actor)


def build_actor(body: dict[str, object]) -> PickupActor | None:
    """Map one introspection body onto a Pickup actor, or None when it proves nothing."""
    if not isinstance(body, dict) or body.get("active") is not True:
        return None
    principal_id = body.get("principal_id")
    if not isinstance(principal_id, str) or not principal_id.strip():
        return None
    status = body.get("status")
    if isinstance(status, str) and status.upper() in _REFUSED_STATUSES:
        return None

    roles = {
        _ROLE_MAP[name]
        for name in _as_str_list(body.get("roles"))
        if name in _ROLE_MAP
    }

    hub_ids: set[UUID] = set()
    merchant_ids: set[UUID] = set()
    for grant in _as_dict_list(body.get("grants")):
        scope_kind = grant.get("scope_kind")
        scope_id = grant.get("scope_id")
        if not isinstance(scope_id, str):
            continue
        try:
            parsed = UUID(scope_id)
        except ValueError:
            # An unparsable scope grants nothing; it is never treated as a wildcard.
            continue
        if scope_kind == "HUB":
            hub_ids.add(parsed)
        elif scope_kind == "MERCHANT":
            merchant_ids.add(parsed)

    return PickupActor(
        actor_id=principal_id,
        roles=frozenset(roles),
        hub_ids=frozenset(hub_ids),
        merchant_ids=frozenset(merchant_ids),
    )


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
