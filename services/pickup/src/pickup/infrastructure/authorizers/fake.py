"""Test-only recovery authorizer — actor identity from cryptographic token map."""

from __future__ import annotations

from uuid import UUID

from pickup.domain.value_objects import RecoveryAction
from pickup.ports.recovery_authorizer import (
    AuthenticatedActor,
    AuthorizerUnavailableError,
    RecoveryAccessDecision,
)


class FakeRecoveryAuthorizer:
    """Deterministic authorizer for HTTP tests — never trusts identity headers."""

    def __init__(
        self,
        *,
        decision: RecoveryAccessDecision | None = None,
        actor_id: str = "test-actor",
        unavailable: bool = False,
        production_ready: bool = True,
        token_actors: dict[str, str] | None = None,
    ) -> None:
        self._decision = decision
        self._actor_id = actor_id
        self._unavailable = unavailable
        self._production_ready = production_ready
        self._token_actors = token_actors or {}
        self.seen_tokens: list[str] = []
        self.seen_actions: list[RecoveryAction] = []

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def authorize_recovery(
        self,
        *,
        bearer_token: str,
        pickup_task_id: UUID,
        action: RecoveryAction,
    ) -> RecoveryAccessDecision:
        self.seen_tokens.append(bearer_token)
        self.seen_actions.append(action)
        _ = pickup_task_id
        if self._unavailable:
            raise AuthorizerUnavailableError("authorizer unavailable")
        if self._decision is not None:
            return self._decision
        actor_id = self._token_actors.get(bearer_token, self._actor_id)
        return RecoveryAccessDecision.allow(AuthenticatedActor(actor_id=actor_id))
