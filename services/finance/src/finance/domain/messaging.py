"""Finance's own integration outbox and inbox records (ADR-0008).

Every service owns these tables. They are deliberately not shared code: a shared ORM
model would couple two services' schemas, which `verify_boundaries.py` forbids. The
*decisions* about them are shared and pure, and live in `messaging_conformance`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    QUARANTINED = "quarantined"


class InboxStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One integration event, written in the same transaction as the state it describes."""

    id: UUID
    event_id: UUID
    subject: str
    event_type: str
    event_version: int
    aggregate_id: UUID
    aggregate_version: int
    payload_json: dict[str, Any]
    status: OutboxStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    created_at: datetime
    processing_owner: str | None = None
    processing_until: datetime | None = None
    published_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


@dataclass(slots=True)
class InboxRecord:
    """One finance of one event to one consumer.

    Uniqueness is `(consumer_name, event_id)`, not `event_id` alone: two consumers in this
    service must each get their own chance to handle the same message, while a refinance
    to the *same* consumer must never produce a second effect.
    """

    inbox_id: UUID
    consumer_name: str
    event_id: UUID
    event_type: str
    event_version: int
    status: InboxStatus
    received_at: datetime
    attempt_count: int = 0
    processing_started_at: datetime | None = None
    processing_lease_until: datetime | None = None
    processed_at: datetime | None = None
    last_error_code: str | None = None
    payload_json: dict[str, Any] = field(default_factory=dict)
