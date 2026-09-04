"""Repository and unit-of-work ports for Shipment acceptance lifecycle."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from shipment.domain.entities import (
    AcceptanceIdempotencyRecord,
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)


class ShipmentRepository(Protocol):
    """Persistence boundary owned by the Shipment service."""

    async def save_order_intent(self, order_intent: OrderIntent) -> None: ...

    async def save_shipment(self, shipment: Shipment) -> None: ...

    async def get_shipment(self, shipment_id: UUID) -> Shipment | None: ...

    async def get_order_intent(self, order_id: UUID) -> OrderIntent | None: ...


class PickupTaskRepository(Protocol):
    """Service-local Pickup prerequisite store — production Pickup adapter deferred."""

    async def save_pickup_task(self, pickup_task: PickupTaskSnapshot) -> None: ...

    async def get_pickup_task(self, pickup_task_id: UUID) -> PickupTaskSnapshot | None: ...

    async def get_pickup_task_for_shipment(
        self, shipment_id: UUID
    ) -> PickupTaskSnapshot | None: ...


class ShipmentEventRepository(Protocol):
    """Append-only shipment event store."""

    async def append_event(self, event: ShipmentEvent) -> None: ...

    async def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]: ...


class AuditLogRepository(Protocol):
    """Append-only audit log store."""

    async def append_entry(self, entry: AuditLogEntry) -> None: ...

    async def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]: ...


class IdempotencyRepository(Protocol):
    """Acceptance command idempotency records."""

    async def save_record(self, record: AcceptanceIdempotencyRecord) -> None: ...

    async def get_record(self, idempotency_key: str) -> AcceptanceIdempotencyRecord | None: ...


class AcceptanceUnitOfWork(Protocol):
    """Atomic acceptance boundary — one transaction for all acceptance effects."""

    shipments: ShipmentRepository
    pickup_tasks: PickupTaskRepository
    shipment_events: ShipmentEventRepository
    audit_logs: AuditLogRepository
    idempotency: IdempotencyRepository

    async def begin(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
