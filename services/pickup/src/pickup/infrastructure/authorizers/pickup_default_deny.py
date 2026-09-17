"""Fail-closed Pickup authorizer until a production identity adapter is wired."""

from __future__ import annotations

from uuid import UUID

from pickup.ports.authorization import PickupAccessDecision, PickupCommand


class DefaultDenyPickupAuthorizer:
    """Rejects every Pickup command — production readiness remains blocked."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize(
        self,
        *,
        bearer_token: str,
        command: PickupCommand,
        resource_id: UUID | None = None,
    ) -> PickupAccessDecision:
        _ = (bearer_token, command, resource_id)
        return PickupAccessDecision.unauthenticated()
