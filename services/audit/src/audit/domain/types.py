"""Service-owned value types. Observations are not canonical Audit facts."""

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
    """ADR-0008 inbox row owned by Audit."""

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
class LegacyAuditObservation:
    """Normalized Legacy Audit observation projection — not a canonical Audit fact."""

    event_id: UUID
    source_system: str
    source_table: str
    source_pk: UUID
    source_position: str
    source_module: str
    audit_entry_id: UUID
    action: str
    entity_type: str
    entity_id: UUID
    actor_type: str
    actor_id: UUID | None
    source: str
    occurred_at: datetime
    bridge_mapper_version: str
    safe_metadata: dict[str, Any]
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ValidatedA2Message:
    """Allowlisted A2 fields after envelope + contract validation."""

    event_id: UUID
    correlation_id: UUID
    source_system: str
    source_table: str
    source_pk: UUID
    source_position: str
    source_module: str
    audit_entry_id: UUID
    action: str
    entity_type: str
    entity_id: UUID
    actor_type: str
    actor_id: UUID | None
    source: str
    occurred_at: datetime
    bridge_mapper_version: str
    safe_metadata: dict[str, Any]
    event_type: str
    event_version: int
