"""Repository port for Shipment acceptance lifecycle."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from shipment.domain.entities import OrderIntent, Shipment


class ShipmentRepository(Protocol):
    """Persistence boundary owned by the Shipment service."""

    def save_order_intent(self, order_intent: OrderIntent) -> None: ...

    def save_shipment(self, shipment: Shipment) -> None: ...

    def get_shipment(self, shipment_id: UUID) -> Shipment | None: ...

    def get_order_intent(self, order_id: UUID) -> OrderIntent | None: ...
