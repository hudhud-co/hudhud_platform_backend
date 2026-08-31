"""Service-owned ports for inbox, observation projection, and transport ACK."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from audit.domain.types import Delivery, InboxRow, LegacyAuditObservation, ValidatedA2Message


class UnitOfWorkPort(Protocol):
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


class ObservationStorePort(Protocol):
    def insert_if_absent(self, observation: LegacyAuditObservation) -> bool:
        """Persist projection. Return False when event_id already projected."""

    def get_by_event_id(self, event_id: UUID) -> LegacyAuditObservation | None: ...

    def get_by_audit_entry_id(self, audit_entry_id: UUID) -> LegacyAuditObservation | None: ...

    def list_by_entity(
        self, *, entity_type: str, entity_id: UUID
    ) -> list[LegacyAuditObservation]: ...

    def list_by_occurred_range(
        self, *, start: datetime, end: datetime
    ) -> list[LegacyAuditObservation]: ...

    def observation_count(self) -> int: ...


class ObservationQueryPort(Protocol):
    """Read-only query boundary for Legacy Audit observations."""

    def get_by_event_id(self, event_id: UUID) -> LegacyAuditObservation | None: ...

    def get_by_audit_entry_id(self, audit_entry_id: UUID) -> LegacyAuditObservation | None: ...

    def list_by_entity(
        self, *, entity_type: str, entity_id: UUID
    ) -> list[LegacyAuditObservation]: ...

    def list_by_occurred_range(
        self, *, start: datetime, end: datetime
    ) -> list[LegacyAuditObservation]: ...


class ConsumerTransportPort(Protocol):
    """Subscriber ACK/NAK/DEFER boundary. Application layer never talks to NATS directly."""

    def ack(self, delivery: Delivery) -> None: ...

    def nak(self, delivery: Delivery) -> None: ...

    def defer(self, delivery: Delivery) -> None: ...


class BrokerAckClient(Protocol):
    """Injected broker client — no embedded credentials in this service."""

    def ack(self, delivery: Delivery) -> None: ...

    def nak(self, delivery: Delivery) -> None: ...

    def defer(self, delivery: Delivery) -> None: ...


class ObservationProjector(Protocol):
    def project(
        self, message: ValidatedA2Message, *, received_at: datetime
    ) -> LegacyAuditObservation: ...


class HandleOutcome:
    """Coordinator result for tests and logging."""

    __slots__ = ("jetstream_action", "inbox_status", "observation_written", "reason")

    def __init__(
        self,
        *,
        jetstream_action: JetStreamConsumerAction,
        inbox_status: InboxStatus | None,
        observation_written: bool,
        reason: str,
    ) -> None:
        self.jetstream_action = jetstream_action
        self.inbox_status = inbox_status
        self.observation_written = observation_written
        self.reason = reason
