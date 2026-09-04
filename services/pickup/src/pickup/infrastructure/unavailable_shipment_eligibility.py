"""Deferred production Shipment eligibility adapter — readiness blocker."""

from __future__ import annotations

from uuid import UUID

from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot


class UnavailableShipmentEligibilityAdapter:
    """Default production composition adapter until Shipment HTTP/event wiring exists."""

    @property
    def is_production_ready(self) -> bool:
        return False

    def get_eligibility(self, shipment_id: UUID) -> ShipmentEligibilitySnapshot | None:
        _ = shipment_id
        return None
