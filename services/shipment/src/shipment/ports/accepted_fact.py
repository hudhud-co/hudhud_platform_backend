"""Ports for native pickup.fact.accepted inbox consumption."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AuditLogEntry,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.types import Delivery, InboxRow, ValidatedPickupAcceptedFact


class FactShipmentRepository(Protocol):
    def save_shipment(self, shipment: Shipment) -> None: ...

    def get_shipment(self, shipment_id: UUID) -> Shipment | None: ...


class FactShipmentEventRepository(Protocol):
    def append_event(self, event: ShipmentEvent) -> None: ...

    def list_events_for_shipment(self, shipment_id: UUID) -> tuple[ShipmentEvent, ...]: ...


class FactAuditLogRepository(Protocol):
    def append_entry(self, entry: AuditLogEntry) -> None: ...

    def list_entries_for_entity(
        self, entity_type: str, entity_id: str
    ) -> tuple[AuditLogEntry, ...]: ...


class AcceptanceDecisionRepository(Protocol):
    def save(self, decision: AcceptanceDecisionRecord) -> None: ...

    def get_for_shipment(self, shipment_id: UUID) -> AcceptanceDecisionRecord | None: ...

    def get_for_pickup_task(self, pickup_task_id: UUID) -> AcceptanceDecisionRecord | None: ...


class AcceptedFactUnitOfWork(Protocol):
    """Atomic native fact boundary — shipment + decision + event + audit + inbox."""

    shipments: FactShipmentRepository
    shipment_events: FactShipmentEventRepository
    audit_logs: FactAuditLogRepository
    acceptance_decisions: AcceptanceDecisionRepository

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class InboxStorePort(Protocol):
    def try_insert_received(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        event_type: str,
        event_version: int,
        handler_version: str,
        processing_owner: str,
        processing_lease_until: datetime,
        received_at: datetime,
        correlation_id: UUID | None,
        jetstream_stream: str | None,
        jetstream_seq: int | None,
        nats_msg_id: str | None,
        aggregate_type: str | None = None,
        aggregate_id: UUID | None = None,
        aggregate_version: int | None = None,
    ) -> InboxRow | None:
        """Insert `(consumer_name, event_id)` or return None on uniqueness conflict."""

    def load_existing(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None: ...

    def reclaim_processing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processing_owner: str,
        processing_lease_until: datetime,
        now: datetime,
    ) -> InboxRow: ...

    def mark_processed(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processed_at: datetime,
    ) -> None: ...

    def mark_quarantined(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        quarantined_at: datetime,
        error_code: str,
        error_message: str,
    ) -> InboxRow: ...


class ConsumerTransportPort(Protocol):
    """ACK/NAK/DEFER boundary — application never talks to NATS directly."""

    def ack(self, delivery: Delivery) -> None: ...

    def nak(self, delivery: Delivery) -> None: ...

    def defer(self, delivery: Delivery) -> None: ...


class HandleOutcome:
    """Coordinator result for tests and logging."""

    __slots__ = (
        "jetstream_action",
        "inbox_status",
        "acceptance_applied",
        "reason",
        "shipment_version",
    )

    def __init__(
        self,
        *,
        jetstream_action: JetStreamConsumerAction,
        inbox_status: InboxStatus | None,
        acceptance_applied: bool,
        reason: str,
        shipment_version: int | None = None,
    ) -> None:
        self.jetstream_action = jetstream_action
        self.inbox_status = inbox_status
        self.acceptance_applied = acceptance_applied
        self.reason = reason
        self.shipment_version = shipment_version


class AcceptedFactApplyPort(Protocol):
    def apply(
        self,
        fact: ValidatedPickupAcceptedFact,
        *,
        recorded_at: datetime,
    ) -> object: ...
