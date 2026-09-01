"""Service-owned value types. Timeline entries are not canonical Shipment facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from messaging_conformance.enums import InboxStatus
from messaging_conformance.values import InboxRecordSnapshot, InboxUniqueKey


@dataclass(frozen=True, slots=True)
class Delivery:
    """Transport-independent JetStream delivery view."""

    body: bytes
    subject: str
    stream: str
    consumer_name: str
    nats_msg_id: str | None = None
    jetstream_seq: int | None = None
    transport_handle: object | None = None


@dataclass
class InboxRow:
    """ADR-0008 inbox row owned by Tracking."""

    id: UUID
    consumer_name: str
    event_id: UUID
    event_type: str
    event_version: int
    status: InboxStatus
    processing_owner: str | None
    processing_lease_until: datetime | None
    handler_version: str
    attempt_count: int
    first_received_at: datetime
    last_received_at: datetime
    processed_at: datetime | None
    quarantined_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    jetstream_stream: str | None
    jetstream_seq: int | None
    correlation_id: UUID | None
    nats_msg_id: str | None
    processing_started_at: datetime | None = None

    def snapshot(self) -> InboxRecordSnapshot:
        return InboxRecordSnapshot(
            key=InboxUniqueKey(consumer_name=self.consumer_name, event_id=self.event_id),
            status=self.status,
            processing_started_at=self.processing_started_at,
            processing_lease_until=self.processing_lease_until,
            attempt_count=self.attempt_count,
        )


@dataclass(frozen=True, slots=True)
class ShipmentTimelineEntry:
    """Legacy A1 observation projection — not Tracking or Shipment authority."""

    event_id: UUID
    shipment_id: UUID
    source_system: str
    source_table: str
    source_pk: UUID
    source_position: str
    source_module: str
    legacy_event_type: str
    occurred_at: datetime
    old_status: str | None
    new_status: str | None
    actor_type: str | None
    actor_id: UUID | None
    bridge_mapper_version: str
    safe_metadata: dict[str, Any]
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ValidatedA1Message:
    """Allowlisted A1 fields after envelope + contract validation."""

    event_id: UUID
    correlation_id: UUID
    shipment_id: UUID
    source_system: str
    source_table: str
    source_pk: UUID
    source_position: str
    source_module: str
    legacy_event_type: str
    occurred_at: datetime
    old_status: str | None
    new_status: str | None
    actor_type: str | None
    actor_id: UUID | None
    bridge_mapper_version: str
    safe_metadata: dict[str, Any]
    event_type: str
    event_version: int


@dataclass(frozen=True, slots=True)
class TimelinePageCursor:
    """Deterministic pagination cursor: occurred_at then event_id tie-breaker."""

    occurred_at: datetime
    event_id: UUID


@dataclass(frozen=True, slots=True)
class TimelinePage:
    """Bounded shipment timeline query page."""

    entries: tuple[ShipmentTimelineEntry, ...]
    next_cursor: TimelinePageCursor | None
