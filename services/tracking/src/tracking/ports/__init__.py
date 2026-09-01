"""Service-owned ports for inbox, timeline projection, and transport ACK."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction

from tracking.domain.types import (
    Delivery,
    InboxRow,
    ShipmentTimelineEntry,
    TimelinePage,
    TimelinePageCursor,
    ValidatedA1Message,
)


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


class TimelineProjectionStorePort(Protocol):
    def insert_if_absent(self, entry: ShipmentTimelineEntry) -> bool:
        """Persist projection. Return False when event_id already projected."""

    def timeline_entry_count(self) -> int: ...


class TimelineQueryPort(Protocol):
    """Read-only query boundary for shipment timeline projections."""

    def get_by_event_id(self, event_id: UUID) -> ShipmentTimelineEntry | None: ...

    def list_by_shipment_id(
        self,
        *,
        shipment_id: UUID,
        cursor: TimelinePageCursor | None = None,
        page_size: int,
    ) -> TimelinePage: ...


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


class TimelineProjector(Protocol):
    def project(
        self, message: ValidatedA1Message, *, received_at: datetime
    ) -> ShipmentTimelineEntry: ...


class HandleOutcome:
    """Coordinator result for tests and logging."""

    __slots__ = ("jetstream_action", "inbox_status", "timeline_written", "reason")

    def __init__(
        self,
        *,
        jetstream_action: JetStreamConsumerAction,
        inbox_status: InboxStatus | None,
        timeline_written: bool,
        reason: str,
    ) -> None:
        self.jetstream_action = jetstream_action
        self.inbox_status = inbox_status
        self.timeline_written = timeline_written
        self.reason = reason
