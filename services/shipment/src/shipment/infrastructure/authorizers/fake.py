"""Explicit fake acceptance authorizer for tests only."""

from __future__ import annotations

from uuid import UUID

from shipment.ports.authorization import (
    AcceptanceAccessDecision,
    AuthenticatedActor,
    AuthorizerUnavailableError,
)


class FakeAcceptanceAuthorizer:
    """Configurable authorizer used by HTTP adapter tests — never for production."""

    def __init__(
        self,
        *,
        actor_user_id: str = "driver-42",
        decision: AcceptanceAccessDecision | None = None,
        unavailable: bool = False,
        production_ready: bool = True,
    ) -> None:
        self._decision = decision or AcceptanceAccessDecision.allow(
            AuthenticatedActor(user_id=actor_user_id)
        )
        self._unavailable = unavailable
        self._production_ready = production_ready
        self.seen_tokens: list[str] = []

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    async def authorize_acceptance_scan(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
        pickup_task_id: UUID,
    ) -> AcceptanceAccessDecision:
        _ = (shipment_id, pickup_task_id)
        self.seen_tokens.append(bearer_token)
        if self._unavailable:
            raise AuthorizerUnavailableError("authorizer unavailable")
        return self._decision
