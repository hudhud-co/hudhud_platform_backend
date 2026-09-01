"""Outbox publisher using messaging_conformance decisions."""

from __future__ import annotations

from typing import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from messaging_conformance import (
    OutboxRecordSnapshot,
    OutboxStatus,
    RetryClassification,
    decide_outbox_publish_result,
    transport_msg_id_for_outbox,
)
from messaging_conformance.retry import classify_retry_error

from legacy_event_bridge.config import DEFAULT_OUTBOX_RETRY_BACKOFF_SECONDS
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.domain.types import OutboxRecord
from legacy_event_bridge.ports import OutboxStorePort, PublisherPort


@dataclass(frozen=True, slots=True)
class PublishBatchOutcome:
    published_count: int
    retry_count: int
    quarantined_count: int


class OutboxPublisher:
    """Claims and publishes outbox rows — landing state is never rolled back."""

    def __init__(
        self,
        *,
        outbox_store: OutboxStorePort,
        publisher: PublisherPort,
        owner_id: str,
        batch_size: int,
        lease_seconds: int,
        retry_backoff_seconds: Sequence[int] = DEFAULT_OUTBOX_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._outbox = outbox_store
        self._publisher = publisher
        self._owner_id = owner_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._retry_backoff_seconds = tuple(retry_backoff_seconds)
        if not self._retry_backoff_seconds:
            msg = "retry_backoff_seconds must contain at least one entry"
            raise ValueError(msg)

    def publish_pending(self) -> PublishBatchOutcome:
        now = datetime.now(tz=UTC)
        self._outbox.recover_stale_processing(now=now)
        lease_until = now + timedelta(seconds=self._lease_seconds)
        claimed = self._outbox.claim_batch(
            owner=self._owner_id,
            batch_size=self._batch_size,
            lease_until=lease_until,
            now=now,
        )

        published = 0
        retries = 0
        quarantined = 0

        for row in claimed:
            snapshot = _to_snapshot(row)
            result = self._publisher.publish(
                subject=row.subject,
                payload_json=row.payload_json,
                transport_msg_id=transport_msg_id_for_outbox(snapshot),
            )
            classification = None
            if not result.ack_received:
                classification = (
                    classify_retry_error(result.error_code)
                    if result.error_code
                    else RetryClassification.TRANSIENT
                )

            decision = decide_outbox_publish_result(
                snapshot,
                broker_ack_received=result.ack_received,
                classification=classification,
            )
            published_at = now if decision.target_status is OutboxStatus.PUBLISHED else None
            next_attempt = None
            if decision.schedule_retry:
                backoff_index = min(row.attempt_count, len(self._retry_backoff_seconds) - 1)
                next_attempt = now + timedelta(seconds=self._retry_backoff_seconds[backoff_index])
                retries += 1
            if decision.target_status is OutboxStatus.QUARANTINED:
                quarantined += 1

            error_code = None if result.ack_received else (result.error_code or "NATS_TIMEOUT")
            error_message = None
            if not result.ack_received:
                error_message = sanitize_error_message(
                    result.error_message or decision.reason,
                )

            self._outbox.apply_publish_decision(
                outbox_id=row.id,
                status=decision.target_status.value,
                clear_owner=decision.clear_owner,
                clear_lease=decision.clear_lease,
                published_at=published_at,
                next_attempt_at=next_attempt or now,
                last_error_code=error_code,
                last_error_message=error_message,
            )
            if result.ack_received:
                published += 1

        return PublishBatchOutcome(
            published_count=published,
            retry_count=retries,
            quarantined_count=quarantined,
        )


def recover_stale_rows(outbox_store: OutboxStorePort, *, now: datetime) -> int:
    return outbox_store.recover_stale_processing(now=now)


def _to_snapshot(row: OutboxRecord) -> OutboxRecordSnapshot:
    return OutboxRecordSnapshot(
        event_id=row.event_id,
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,
    )
