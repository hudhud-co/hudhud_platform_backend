"""Notification's own durable inbox record (ADR-0008).

Every service owns this table. It is deliberately not shared code: a shared ORM model
would couple two services' schemas, which `verify_boundaries.py` forbids. The *decisions*
about it are shared and pure, and live in `messaging_conformance`.

There is no outbox here. Notification consumes journey facts and publishes none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class InboxStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


@dataclass(slots=True)
class InboxRecord:
    """One delivery of one event to one consumer.

    Uniqueness is `(consumer_name, event_id)`, not `event_id` alone: two consumers in this
    service must each get their own chance to handle the same message, while a redelivery
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
