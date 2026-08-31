"""Protocol ports for service-owned outbox/inbox adapters."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from messaging_conformance.enums import InboxStatus
from messaging_conformance.values import InboxRecordSnapshot, OutboxRecordSnapshot


class OutboxStorePort(Protocol):
    """Service-owned outbox persistence port — no shared ORM implementation."""

    def insert_pending(
        self,
        *,
        event_id: UUID,
        subject: str,
        payload_json: dict[str, object],
        next_attempt_at: datetime,
    ) -> OutboxRecordSnapshot:
        """Insert a pending outbox row in the same transaction as domain writes."""

    def recover_stale_claims(self, *, now: datetime) -> int:
        """Requeue rows whose processing lease expired."""

    def claim_batch(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_duration: timedelta,
        now: datetime,
    ) -> list[OutboxRecordSnapshot]:
        """Claim pending or expired-processing rows for relay publish."""

    def mark_publish_outcome(self, row: OutboxRecordSnapshot, *, decision_reason: str) -> None:
        """Apply publish transition decided by :mod:`outbox_decisions`."""


class InboxStorePort(Protocol):
    """Service-owned inbox persistence port — no shared ORM implementation."""

    def try_insert_received(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        received_at: datetime,
    ) -> InboxRecordSnapshot | None:
        """Insert `(consumer_name, event_id)` or return ``None`` on conflict."""

    def load_existing(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
    ) -> InboxRecordSnapshot | None:
        """Load an existing inbox row for duplicate handling."""

    def mark_status(
        self,
        *,
        key_event_id: UUID,
        consumer_name: str,
        status: InboxStatus,
        processing_lease_until: datetime | None = None,
    ) -> None:
        """Update inbox status within the handler transaction."""


class OutboxRelayPort(Protocol):
    """Relay runtime port — implemented by each service composition root."""

    def publish_to_jetstream(
        self,
        *,
        subject: str,
        payload_json: dict[str, object],
        nats_msg_id: str,
    ) -> bool:
        """Publish and return True only when the broker acknowledges delivery."""


class InboxHandlerPort(Protocol):
    """Consumer handler port — domain effects remain service-owned."""

    def handle_envelope(self, *, payload_json: dict[str, object]) -> None:
        """Apply idempotent domain/projection effects for one envelope."""
