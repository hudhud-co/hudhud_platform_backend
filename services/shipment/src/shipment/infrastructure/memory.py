"""In-memory Shipment repository for unit tests."""

from __future__ import annotations

from uuid import UUID

from shipment.domain.entities import OrderIntent, Shipment


class InMemoryShipmentRepository:
    """Shipment-local persistence fake — not shared across services."""

    def __init__(self) -> None:
        self._orders: dict[UUID, OrderIntent] = {}
        self._shipments: dict[UUID, Shipment] = {}

    def save_order_intent(self, order_intent: OrderIntent) -> None:
        self._orders[order_intent.order_id] = order_intent

    def save_shipment(self, shipment: Shipment) -> None:
        self._shipments[shipment.shipment_id] = shipment

    def get_shipment(self, shipment_id: UUID) -> Shipment | None:
        return self._shipments.get(shipment_id)

    def get_order_intent(self, order_id: UUID) -> OrderIntent | None:
        return self._orders.get(order_id)
