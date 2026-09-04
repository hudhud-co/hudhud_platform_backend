"""In-memory accepted-fact unit of work, inbox, and recording transport."""

from __future__ import annotations

import copy
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AuditLogEntry,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.errors import RetryableHandlerError
from shipment.domain.sanitize import sanitize_error_message
from shipment.domain.types import Delivery, InboxRow


class SimulatedCrash(RuntimeError):
    """Test hook: commit succeeded, ACK not yet sent."""


class SimulatedCommitFailure(RuntimeError):
    """Test hook: forces rollback before commit completes."""


class RecordingTransport:
    """Records ACK/NAK/DEFER in the same action log as unit-of-work commits."""

    def __init__(self, store: MemoryAcceptedFactStore) -> None:
        self._store = store

    def ack(self, delivery: Delivery) -> None:
        if self._store.crash_before_ack:
            self._store.crash_before_ack = False
            raise SimulatedCrash("crash after commit before ack")
        self._store.actions.append("ack")
        self._store.transport_actions.append(JetStreamConsumerAction.ACK)
        self._store.last_delivery = delivery

    def nak(self, delivery: Delivery) -> None:
        self._store.actions.append("nak")
        self._store.transport_actions.append(JetStreamConsumerAction.NAK)
        self._store.last_delivery = delivery

    def defer(self, delivery: Delivery) -> None:
        self._store.actions.append("defer")
        self._store.transport_actions.append(JetStreamConsumerAction.DEFER)
        self._store.last_delivery = delivery


class MemoryAcceptedFactStore:
    """Copy-on-write in-memory persistence for accepted-fact coordinator tests."""

    def __init__(self) -> None:
        self._shipments: dict[UUID, Shipment] = {}
        self._shipment_events: list[ShipmentEvent] = []
        self._audit_logs: list[AuditLogEntry] = []
        self._decisions: dict[UUID, AcceptanceDecisionRecord] = {}
        self._decisions_by_pickup: dict[UUID, UUID] = {}
        self._inbox: dict[tuple[str, UUID], InboxRow] = {}
        self._tx_shipments: dict[UUID, Shipment] | None = None
        self._tx_shipment_events: list[ShipmentEvent] | None = None
        self._tx_audit_logs: list[AuditLogEntry] | None = None
        self._tx_decisions: dict[UUID, AcceptanceDecisionRecord] | None = None
        self._tx_decisions_by_pickup: dict[UUID, UUID] | None = None
        self._tx_inbox: dict[tuple[str, UUID], InboxRow] | None = None
        self.actions: list[str] = []
        self.transport_actions: list[JetStreamConsumerAction] = []
        self.last_delivery: Delivery | None = None
        self.fail_on_commit = False
        self.fail_next_apply = False
        self.fail_next_quarantine = False
        self.crash_before_ack = False
        self.pickup_task_reads = 0

    @property
    def shipments(self) -> _ShipmentRepo:
        return _ShipmentRepo(self)

    @property
    def shipment_events(self) -> _ShipmentEventRepo:
        return _ShipmentEventRepo(self)

    @property
    def audit_logs(self) -> _AuditLogRepo:
        return _AuditLogRepo(self)

    @property
    def acceptance_decisions(self) -> _DecisionRepo:
        return _DecisionRepo(self)

    def begin(self) -> None:
        self._tx_shipments = copy.deepcopy(self._shipments)
        self._tx_shipment_events = copy.deepcopy(self._shipment_events)
        self._tx_audit_logs = copy.deepcopy(self._audit_logs)
        self._tx_decisions = copy.deepcopy(self._decisions)
        self._tx_decisions_by_pickup = copy.deepcopy(self._decisions_by_pickup)
        self._tx_inbox = copy.deepcopy(self._inbox)
        self.actions.append("begin")

    def commit(self) -> None:
        if self._tx_shipments is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        if self.fail_on_commit:
            self.rollback()
            raise SimulatedCommitFailure("simulated commit failure")
        self._shipments = self._tx_shipments
        self._shipment_events = self._tx_shipment_events  # type: ignore[assignment]
        self._audit_logs = self._tx_audit_logs  # type: ignore[assignment]
        self._decisions = self._tx_decisions  # type: ignore[assignment]
        self._decisions_by_pickup = self._tx_decisions_by_pickup  # type: ignore[assignment]
        self._inbox = self._tx_inbox  # type: ignore[assignment]
        self._clear_tx()
        self.actions.append("commit")

    def rollback(self) -> None:
        self._clear_tx()
        self.actions.append("rollback")

    def _clear_tx(self) -> None:
        self._tx_shipments = None
        self._tx_shipment_events = None
        self._tx_audit_logs = None
        self._tx_decisions = None
        self._tx_decisions_by_pickup = None
        self._tx_inbox = None

    def seed_shipment(self, shipment: Shipment) -> None:
        self._shipments[shipment.shipment_id] = copy.deepcopy(shipment)

    def seed_decision(self, decision: AcceptanceDecisionRecord) -> None:
        self._decisions[decision.shipment_id] = copy.deepcopy(decision)
        self._decisions_by_pickup[decision.pickup_task_id] = decision.shipment_id

    def seed_inbox(self, row: InboxRow) -> None:
        self._inbox[(row.consumer_name, row.event_id)] = copy.deepcopy(row)

    def committed_inbox(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        row = self._inbox.get((consumer_name, event_id))
        return copy.deepcopy(row) if row is not None else None

    def try_insert_received(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        event_type: str,
        event_version: int,
        handler_version: str,
        processing_owner: str,
        processing_lease_until: object,
        received_at: object,
        correlation_id: object,
        jetstream_stream: str | None,
        jetstream_seq: int | None,
        nats_msg_id: str | None,
        aggregate_type: str | None = None,
        aggregate_id: object | None = None,
        aggregate_version: int | None = None,
    ) -> InboxRow | None:
        working = self._working_inbox()
        key = (consumer_name, event_id)
        if key in working:
            return None
        row = InboxRow(
            id=uuid4(),
            consumer_name=consumer_name,
            event_id=event_id,
            event_type=event_type,
            event_version=event_version,
            status=InboxStatus.PROCESSING,
            processing_owner=processing_owner,
            processing_lease_until=processing_lease_until,  # type: ignore[arg-type]
            handler_version=handler_version,
            attempt_count=1,
            first_received_at=received_at,  # type: ignore[arg-type]
            last_received_at=received_at,  # type: ignore[arg-type]
            processed_at=None,
            quarantined_at=None,
            last_error_code=None,
            last_error_message=None,
            jetstream_stream=jetstream_stream,
            jetstream_seq=jetstream_seq,
            correlation_id=correlation_id,  # type: ignore[arg-type]
            nats_msg_id=nats_msg_id,
            processing_started_at=received_at,  # type: ignore[arg-type]
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,  # type: ignore[arg-type]
            aggregate_version=aggregate_version,
        )
        working[key] = row
        return copy.deepcopy(row)

    def load_existing(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        working = self._working_inbox()
        row = working.get((consumer_name, event_id))
        return copy.deepcopy(row) if row is not None else None

    def reclaim_processing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processing_owner: str,
        processing_lease_until: object,
        now: object,
    ) -> InboxRow:
        working = self._working_inbox()
        row = working[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSING
        row.processing_owner = processing_owner
        row.processing_lease_until = processing_lease_until  # type: ignore[assignment]
        row.attempt_count += 1
        row.last_received_at = now  # type: ignore[assignment]
        row.processing_started_at = now  # type: ignore[assignment]
        return copy.deepcopy(row)

    def mark_processed(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        processed_at: object,
    ) -> None:
        row = self._working_inbox()[(consumer_name, event_id)]
        row.status = InboxStatus.PROCESSED
        row.processed_at = processed_at  # type: ignore[assignment]
        row.processing_owner = None
        row.processing_lease_until = None

    def mark_quarantined(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        quarantined_at: object,
        error_code: str,
        error_message: str,
    ) -> InboxRow:
        if self.fail_next_quarantine:
            self.fail_next_quarantine = False
            raise RetryableHandlerError("DB_DEADLOCK", "injected quarantine persistence failure")
        working = self._working_inbox()
        key = (consumer_name, event_id)
        row = working[key]
        row.status = InboxStatus.QUARANTINED
        row.quarantined_at = quarantined_at  # type: ignore[assignment]
        row.last_error_code = error_code
        row.last_error_message = sanitize_error_message(error_message)
        row.processing_owner = None
        row.processing_lease_until = None
        return copy.deepcopy(row)

    def _working_shipments(self) -> dict[UUID, Shipment]:
        if self._tx_shipments is not None:
            return self._tx_shipments
        return self._shipments

    def _working_shipment_events(self) -> list[ShipmentEvent]:
        if self._tx_shipment_events is not None:
            return self._tx_shipment_events
        return self._shipment_events

    def _working_audit_logs(self) -> list[AuditLogEntry]:
        if self._tx_audit_logs is not None:
            return self._tx_audit_logs
        return self._audit_logs

    def _working_decisions(self) -> dict[UUID, AcceptanceDecisionRecord]:
        if self._tx_decisions is not None:
            return self._tx_decisions
        return self._decisions

    def _working_decisions_by_pickup(self) -> dict[UUID, UUID]:
        if self._tx_decisions_by_pickup is not None:
            return self._tx_decisions_by_pickup
        return self._decisions_by_pickup

    def _working_inbox(self) -> dict[tuple[str, UUID], InboxRow]:
        if self._tx_inbox is not None:
            return self._tx_inbox
        return self._inbox


class _ShipmentRepo:
    def __init__(self, store: MemoryAcceptedFactStore) -> None:
        self._store = store

    def save_shipment(self, shipment: Shipment) -> None:
        if self._store.fail_next_apply:
            self._store.fail_next_apply = False
            raise RetryableHandlerError("DB_DEADLOCK", "injected apply failure")
        self._store._working_shipments()[shipment.shipment_id] = copy.deepcopy(shipment)

    def get_shipment(self, shipment_id: UUID) -> Shipment | None:
        shipment = self._store._working_shipments().get(shipment_id)
        return copy.deepcopy(shipment) if shipment is not None else None


class _ShipmentEventRepo:
    def __init__(self, store: MemoryAcceptedFactStore) -> None:
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
    def __init__(self, store: MemoryAcceptedFactStore) -> None:
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


class _DecisionRepo:
    def __init__(self, store: MemoryAcceptedFactStore) -> None:
        self._store = store

    def save(self, decision: AcceptanceDecisionRecord) -> None:
        self._store._working_decisions()[decision.shipment_id] = copy.deepcopy(decision)
        self._store._working_decisions_by_pickup()[decision.pickup_task_id] = decision.shipment_id

    def get_for_shipment(self, shipment_id: UUID) -> AcceptanceDecisionRecord | None:
        decision = self._store._working_decisions().get(shipment_id)
        return copy.deepcopy(decision) if decision is not None else None

    def get_for_pickup_task(self, pickup_task_id: UUID) -> AcceptanceDecisionRecord | None:
        shipment_id = self._store._working_decisions_by_pickup().get(pickup_task_id)
        if shipment_id is None:
            return None
        return self.get_for_shipment(shipment_id)
