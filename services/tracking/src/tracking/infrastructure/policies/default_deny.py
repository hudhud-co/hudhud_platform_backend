"""Default-deny shipment access until a real policy adapter is wired."""

from __future__ import annotations

from uuid import UUID

from tracking.ports.query_authorizer import TrackingAccessDecision


class DefaultDenyShipmentAccessPolicy:
    """Authenticated subjects are denied shipment reads — readiness remains blocked."""

    @property
    def is_production_ready(self) -> bool:
        return False

    async def authorize_shipment_timeline_read(
        self,
        *,
        subject_id: UUID,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        _ = subject_id, shipment_id
        return TrackingAccessDecision.forbidden()
