"""Service-owned shipment access policy — separate from JWT authentication."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from tracking.ports.query_authorizer import TrackingAccessDecision


class ShipmentAccessPolicy(Protocol):
    """Domain shipment authorization after cryptographic identity is verified."""

    @property
    def is_production_ready(self) -> bool:
        """True when a real policy adapter is configured (not default-deny)."""

    async def authorize_shipment_timeline_read(
        self,
        *,
        subject_id: UUID,
        shipment_id: UUID,
    ) -> TrackingAccessDecision: ...
