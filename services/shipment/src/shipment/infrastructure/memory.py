"""In-memory acceptance persistence with rollback-safe unit of work."""

from __future__ import annotations

import copy
from uuid import UUID

from shipment.domain.entities import (
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)


class SimulatedCommitFailure(RuntimeError):
    """Test hook: forces rollback before commit completes."""


class InMemoryAcceptanceUnitOfWork:
    """Rollback-safe in-memory UoW for W11 acceptance unit tests only."""

    def __init__(self) -> None:
        self._orders: dict[UUID, OrderIntent] = {}
        self._shipments: dict[UUID, Shipment] = {}
        self._pickup_tasks: dict[UUID, PickupTaskSnapshot] = {}
        self._pickup_tasks_by_shipment: dict[UUID, UUID] = {}
        self._shipment_events: list[ShipmentEvent] = []
        self._audit_logs: list[AuditLogEntry] = []
        self._tx_orders: dict[UUID, OrderIntent] | None = None
        self._tx_shipments: dict[UUID, Shipment] | None = None
        self._tx_pickup_tasks: dict[UUID, PickupTaskSnapshot] | None = None
        self._tx_pickup_tasks_by_shipment: dict[UUID, UUID] | None = None
        self._tx_shipment_events: list[ShipmentEvent] | None = None
        self._tx_audit_logs: list[AuditLogEntry] | None = None
        self.fail_on_commit = False
        self.actions: list[str] = []

    @property
    def shipments(self) -> _ShipmentRepo:
        return _ShipmentRepo(self)

    @property
    def pickup_tasks(self) -> _PickupTaskRepo:
        return _PickupTaskRepo(self)

    @property
    def shipment_events(self) -> _ShipmentEventRepo:
        return _ShipmentEventRepo(self)

    @property
    def audit_logs(self) -> _AuditLogRepo:
        return _AuditLogRepo(self)

    def begin(self) -> None:
        self._tx_orders = copy.deepcopy(self._orders)
        self._tx_shipments = copy.deepcopy(self._shipments)
        self._tx_pickup_tasks = copy.deepcopy(self._pickup_tasks)
        self._tx_pickup_tasks_by_shipment = copy.deepcopy(self._pickup_tasks_by_shipment)
        self._tx_shipment_events = copy.deepcopy(self._shipment_events)
        self._tx_audit_logs = copy.deepcopy(self._audit_logs)
        self.actions.append("begin")

    def commit(self) -> None:
        if self._tx_orders is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        if self.fail_on_commit:
            self.rollback()
            raise SimulatedCommitFailure("simulated commit failure")
        self._orders = self._tx_orders
        self._shipments = self._tx_shipments
        self._pickup_tasks = self._tx_pickup_tasks
        self._pickup_tasks_by_shipment = self._tx_pickup_tasks_by_shipment
        self._shipment_events = self._tx_shipment_events
        self._audit_logs = self._tx_audit_logs
        self._tx_orders = None
        self._tx_shipments = None
        self._tx_pickup_tasks = None
        self._tx_pickup_tasks_by_shipment = None
        self._tx_shipment_events = None
        self._tx_audit_logs = None
        self.actions.append("commit")

    def rollback(self) -> None:
        self._tx_orders = None
        self._tx_shipments = None
        self._tx_pickup_tasks = None
        self._tx_pickup_tasks_by_shipment = None
        self._tx_shipment_events = None
        self._tx_audit_logs = None
        self.actions.append("rollback")

    def _working_orders(self) -> dict[UUID, OrderIntent]:
        if self._tx_orders is not None:
            return self._tx_orders
        return self._orders

    def _working_shipments(self) -> dict[UUID, Shipment]:
        if self._tx_shipments is not None:
            return self._tx_shipments
        return self._shipments

    def _working_pickup_tasks(self) -> dict[UUID, PickupTaskSnapshot]:
        if self._tx_pickup_tasks is not None:
            return self._tx_pickup_tasks
        return self._pickup_tasks

    def _working_pickup_tasks_by_shipment(self) -> dict[UUID, UUID]:
        if self._tx_pickup_tasks_by_shipment is not None:
            return self._tx_pickup_tasks_by_shipment
        return self._pickup_tasks_by_shipment

    def _working_shipment_events(self) -> list[ShipmentEvent]:
        if self._tx_shipment_events is not None:
            return self._tx_shipment_events
        return self._shipment_events

    def _working_audit_logs(self) -> list[AuditLogEntry]:
        if self._tx_audit_logs is not None:
            return self._tx_audit_logs
        return self._audit_logs


class _ShipmentRepo:
    def __init__(self, store: InMemoryAcceptanceUnitOfWork) -> None:
        self._store = store

    def save_order_intent(self, order_intent: OrderIntent) -> None:
        self._store._working_orders()[order_intent.order_id] = copy.deepcopy(order_intent)

    def save_shipment(self, shipment: Shipment) -> None:
        self._store._working_shipments()[shipment.shipment_id] = copy.deepcopy(shipment)

    def get_shipment(self, shipment_id: UUID) -> Shipment | None:
        shipment = self._store._working_shipments().get(shipment_id)
        return copy.deepcopy(shipment) if shipment is not None else None

    def get_order_intent(self, order_id: UUID) -> OrderIntent | None:
        order = self._store._working_orders().get(order_id)
        return copy.deepcopy(order) if order is not None else None


class _PickupTaskRepo:
    def __init__(self, store: InMemoryAcceptanceUnitOfWork) -> None:
        self._store = store

    def save_pickup_task(self, pickup_task: PickupTaskSnapshot) -> None:
        self._store._working_pickup_tasks()[pickup_task.pickup_task_id] = copy.deepcopy(pickup_task)
        self._store._working_pickup_tasks_by_shipment()[pickup_task.shipment_id] = (
            pickup_task.pickup_task_id
        )

    def get_pickup_task(self, pickup_task_id: UUID) -> PickupTaskSnapshot | None:
        task = self._store._working_pickup_tasks().get(pickup_task_id)
        return copy.deepcopy(task) if task is not None else None

    def get_pickup_task_for_shipment(self, shipment_id: UUID) -> PickupTaskSnapshot | None:
        task_id = self._store._working_pickup_tasks_by_shipment().get(shipment_id)
        if task_id is None:
            return None
        return self.get_pickup_task(task_id)


class _ShipmentEventRepo:
    def __init__(self, store: InMemoryAcceptanceUnitOfWork) -> None:
        self._store = store

    def append_event(self, event: ShipmentEvent) -> None:
        self._store._working_shipment_events().append(copy.deepcopy(event))

    def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]:
        return tuple(
            copy.deepcopy(event)
            for event in self._store._working_shipment_events()
            if event.shipment_id == shipment_id
        )


class _AuditLogRepo:
    def __init__(self, store: InMemoryAcceptanceUnitOfWork) -> None:
        self._store = store

    def append_entry(self, entry: AuditLogEntry) -> None:
        self._store._working_audit_logs().append(copy.deepcopy(entry))

    def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]:
        return tuple(
            copy.deepcopy(entry)
            for entry in self._store._working_audit_logs()
            if entry.entity_type == entity_type and entry.entity_id == entity_id
        )


# Backward-compatible alias for tests referencing the old repository name.
InMemoryShipmentRepository = InMemoryAcceptanceUnitOfWork
