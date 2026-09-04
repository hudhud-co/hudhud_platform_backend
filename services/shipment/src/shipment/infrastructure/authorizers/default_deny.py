"""Fail-closed authorization adapter until a production identity adapter is wired."""

from __future__ import annotations

from uuid import UUID

from shipment.ports.authorization import AcceptanceAccessDecision


class DefaultDenyAcceptanceAuthorizer:
    """Rejects all acceptance commands — production readiness remains blocked."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize_acceptance_scan(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
        pickup_task_id: UUID,
    ) -> AcceptanceAccessDecision:
        _ = (bearer_token, shipment_id, pickup_task_id)
        return AcceptanceAccessDecision.unauthenticated()
