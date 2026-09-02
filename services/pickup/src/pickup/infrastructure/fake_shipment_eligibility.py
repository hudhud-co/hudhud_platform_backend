"""In-memory Shipment eligibility adapter for W12 unit tests only."""

from __future__ import annotations

from uuid import UUID

from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot


class InMemoryShipmentEligibilityAdapter:
    """Fake Shipment eligibility store — production HTTP/event adapter deferred."""

    def __init__(self) -> None:
        self._snapshots: dict[UUID, ShipmentEligibilitySnapshot] = {}

    def seed(self, snapshot: ShipmentEligibilitySnapshot) -> None:
        self._snapshots[snapshot.shipment_id] = snapshot

    def get_eligibility(self, shipment_id: UUID) -> ShipmentEligibilitySnapshot | None:
        return self._snapshots.get(shipment_id)
