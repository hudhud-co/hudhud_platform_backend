"""Immutable technical value objects for outbox/inbox decision inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from messaging_conformance.enums import InboxStatus, OutboxStatus


@dataclass(frozen=True, slots=True)
class InboxUniqueKey:
    """Authoritative inbox deduplication key (ADR-0002 / ADR-0008)."""

    consumer_name: str
    event_id: UUID


@dataclass(frozen=True, slots=True)
class InboxRecordSnapshot:
    """Read-only inbox row view for pure decision functions."""

    key: InboxUniqueKey
    status: InboxStatus
    processing_started_at: datetime | None = None
    processing_lease_until: datetime | None = None
    attempt_count: int = 0


@dataclass(frozen=True, slots=True)
class OutboxRecordSnapshot:
    """Read-only outbox row view for pure decision functions."""

    event_id: UUID
    status: OutboxStatus
    processing_owner: str | None = None
    processing_until: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 0
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TransportDedupeBoundary:
    """Documents NATS transport dedupe vs service-owned idempotency."""

    transport_mechanism: str
    transport_authority: str
    consumer_idempotency_authority: str

    @classmethod
    def nats_msg_id_default(cls) -> TransportDedupeBoundary:
        return cls(
            transport_mechanism="Nats-Msg-Id",
            transport_authority="bounded_transport_dedupe_only",
            consumer_idempotency_authority="inbox_unique_consumer_name_event_id",
        )
