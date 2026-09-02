"""Repository and unit-of-work ports for Shipment acceptance lifecycle."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from shipment.domain.entities import (
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)


class ShipmentRepository(Protocol):
    """Persistence boundary owned by the Shipment service."""

    def save_order_intent(self, order_intent: OrderIntent) -> None: ...

    def save_shipment(self, shipment: Shipment) -> None: ...

    def get_shipment(self, shipment_id: UUID) -> Shipment | None: ...

    def get_order_intent(self, order_id: UUID) -> OrderIntent | None: ...


class PickupTaskRepository(Protocol):
    """Service-local Pickup prerequisite store — production Pickup adapter deferred."""

    def save_pickup_task(self, pickup_task: PickupTaskSnapshot) -> None: ...

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTaskSnapshot | None: ...

    def get_pickup_task_for_shipment(self, shipment_id: UUID) -> PickupTaskSnapshot | None: ...


class ShipmentEventRepository(Protocol):
    """Append-only shipment event store."""

    def append_event(self, event: ShipmentEvent) -> None: ...

    def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]: ...


class AuditLogRepository(Protocol):
    """Append-only audit log store."""

    def append_entry(self, entry: AuditLogEntry) -> None: ...

    def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]: ...


class AcceptanceUnitOfWork(Protocol):
    """Atomic acceptance boundary — one transaction for all acceptance effects."""

    shipments: ShipmentRepository
    pickup_tasks: PickupTaskRepository
    shipment_events: ShipmentEventRepository
    audit_logs: AuditLogRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
