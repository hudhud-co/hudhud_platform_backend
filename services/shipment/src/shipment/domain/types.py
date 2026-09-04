"""Service-owned types for pickup.fact.accepted inbox consumption."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from messaging_conformance.enums import InboxStatus
from messaging_conformance.values import InboxRecordSnapshot, InboxUniqueKey

from shipment.domain.value_objects import AcceptanceOutcome, EvidenceReference


@dataclass(frozen=True, slots=True)
class Delivery:
    """Transport-independent delivery view (no live NATS coupling)."""

    body: bytes
    subject: str
    stream: str
    consumer_name: str
    nats_msg_id: str | None = None
    jetstream_seq: int | None = None
    transport_handle: object | None = None


@dataclass
class InboxRow:
    """ADR-0008 inbox row owned by Shipment."""

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
    aggregate_type: str | None = None
    aggregate_id: UUID | None = None
    aggregate_version: int | None = None

    def snapshot(self) -> InboxRecordSnapshot:
        return InboxRecordSnapshot(
            key=InboxUniqueKey(consumer_name=self.consumer_name, event_id=self.event_id),
            status=self.status,
            processing_started_at=self.processing_started_at,
            processing_lease_until=self.processing_lease_until,
            attempt_count=self.attempt_count,
        )


@dataclass(frozen=True, slots=True)
class ValidatedPickupAcceptedFact:
    """Allowlisted pickup.fact.accepted fields after envelope + contract validation."""

    event_id: UUID
    correlation_id: UUID
    event_type: str
    event_version: int
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    pickup_task_id: UUID
    shipment_id: UUID
    outcome: AcceptanceOutcome
    accepted_at: datetime
    assigned_driver_user_id: str
    acting_driver_user_id: str
    scanned_identifier: str
    exception_evidence: tuple[EvidenceReference, ...]
