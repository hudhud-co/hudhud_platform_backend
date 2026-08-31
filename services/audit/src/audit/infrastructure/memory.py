"""In-memory inbox, observation store, unit of work, and recording transport."""

from __future__ import annotations

import copy
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from audit.domain.errors import RetryableHandlerError
from audit.domain.sanitize import sanitize_error_message
from audit.domain.types import Delivery, InboxRow, LegacyAuditObservation


class RecordingTransport:
    """Records ACK/NAK/DEFER in the same action log as unit-of-work commits."""

    def __init__(self, store: MemoryAuditStore) -> None:
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


class SimulatedCrash(RuntimeError):
    """Test hook: commit succeeded, ACK not yet sent."""


class MemoryAuditStore:
    """Copy-on-write in-memory persistence for coordinator tests."""

    def __init__(self) -> None:
        self._inbox: dict[tuple[str, UUID], InboxRow] = {}
        self._observations: dict[UUID, LegacyAuditObservation] = {}
        self._tx_inbox: dict[tuple[str, UUID], InboxRow] | None = None
        self._tx_observations: dict[UUID, LegacyAuditObservation] | None = None
        self.actions: list[str] = []
        self.transport_actions: list[JetStreamConsumerAction] = []
        self.last_delivery: Delivery | None = None
        self.fail_next_projection = False
        self.fail_next_quarantine = False
        self.crash_before_ack = False

    def begin(self) -> None:
        self._tx_inbox = copy.deepcopy(self._inbox)
        self._tx_observations = copy.deepcopy(self._observations)

    def commit(self) -> None:
        if self._tx_inbox is None or self._tx_observations is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._inbox = self._tx_inbox
        self._observations = self._tx_observations
        self._tx_inbox = None
        self._tx_observations = None
        self.actions.append("commit")

    def rollback(self) -> None:
        self._tx_inbox = None
        self._tx_observations = None
        self.actions.append("rollback")

    def seed_inbox(self, row: InboxRow) -> None:
        self._inbox[(row.consumer_name, row.event_id)] = copy.deepcopy(row)

    def seed_observation(self, observation: LegacyAuditObservation) -> None:
        self._observations[observation.event_id] = observation

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

    def insert_if_absent(self, observation: LegacyAuditObservation) -> bool:
        if self.fail_next_projection:
            self.fail_next_projection = False
            raise RetryableHandlerError("DB_DEADLOCK", "injected projection failure")
        working = self._working_observations()
        if observation.event_id in working:
            return False
        working[observation.event_id] = observation
        return True

    def get_by_event_id(self, event_id: UUID) -> LegacyAuditObservation | None:
        return self._observations.get(event_id)

    def get_by_audit_entry_id(self, audit_entry_id: UUID) -> LegacyAuditObservation | None:
        for item in self._observations.values():
            if item.audit_entry_id == audit_entry_id:
                return item
        return None

    def list_by_entity(
        self, *, entity_type: str, entity_id: UUID
    ) -> list[LegacyAuditObservation]:
        return [
            item
            for item in self._observations.values()
            if item.entity_type == entity_type and item.entity_id == entity_id
        ]

    def list_by_occurred_range(
        self, *, start: object, end: object
    ) -> list[LegacyAuditObservation]:
        return [
            item
            for item in self._observations.values()
            if start <= item.occurred_at <= end  # type: ignore[operator]
        ]

    def observation_count(self) -> int:
        return len(self._observations)

    def committed_inbox(self, *, consumer_name: str, event_id: UUID) -> InboxRow | None:
        row = self._inbox.get((consumer_name, event_id))
        return copy.deepcopy(row) if row is not None else None

    def _working_inbox(self) -> dict[tuple[str, UUID], InboxRow]:
        if self._tx_inbox is None:
            msg = "inbox mutation outside transaction"
            raise RuntimeError(msg)
        return self._tx_inbox

    def _working_observations(self) -> dict[UUID, LegacyAuditObservation]:
        if self._tx_observations is None:
            msg = "observation mutation outside transaction"
            raise RuntimeError(msg)
        return self._tx_observations
