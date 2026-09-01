"""Fail-closed authorizer for composition root until ADR-0004 adapter is wired."""

from __future__ import annotations

from uuid import UUID

from tracking.ports.query_authorizer import TrackingAccessDecision


class DefaultDenyQueryAuthorizer:
    """Rejects all timeline reads — production readiness remains blocked."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize_timeline_read(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        return TrackingAccessDecision.unauthenticated()
