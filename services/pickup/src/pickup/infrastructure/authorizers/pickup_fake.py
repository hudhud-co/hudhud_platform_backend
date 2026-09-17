"""Test-only Pickup authorizer — actor identity resolved from a token map."""

from __future__ import annotations

from uuid import UUID

from pickup.ports.authorization import (
    AuthorizationOutcome,
    PickupAccessDecision,
    PickupActor,
    PickupCommand,
)
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError


class FakePickupAuthorizer:
    """Deterministic authorizer for tests — never trusts identity headers."""

    def __init__(
        self,
        *,
        token_actors: dict[str, PickupActor] | None = None,
        default_actor: PickupActor | None = None,
        denied_commands: frozenset[PickupCommand] | None = None,
        unavailable: bool = False,
        production_ready: bool = True,
    ) -> None:
        self._token_actors = token_actors or {}
        self._default_actor = default_actor
        self._denied_commands = denied_commands or frozenset()
        self._unavailable = unavailable
        self._production_ready = production_ready
        self.seen: list[tuple[str, PickupCommand, UUID | None]] = []

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: PickupCommand,
        resource_id: UUID | None = None,
    ) -> PickupAccessDecision:
        self.seen.append((bearer_token, command, resource_id))
        if self._unavailable:
            raise AuthorizerUnavailableError("authorizer unavailable")
        if command in self._denied_commands:
            return PickupAccessDecision.forbidden()
        actor = self._token_actors.get(bearer_token, self._default_actor)
        if actor is None:
            return PickupAccessDecision(outcome=AuthorizationOutcome.UNAUTHENTICATED)
        return PickupAccessDecision.allow(actor)
