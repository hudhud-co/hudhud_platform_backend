"""Pure outbox claim, recovery, and publish decision functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from messaging_conformance.enums import OutboxStatus, RetryClassification
from messaging_conformance.lease import is_lease_expired
from messaging_conformance.retry import should_quarantine
from messaging_conformance.values import OutboxRecordSnapshot


@dataclass(frozen=True, slots=True)
class OutboxRecoveryDecision:
    """Decision to recover a stale processing claim."""

    target_status: OutboxStatus
    clear_owner: bool
    clear_lease: bool
    reason: str


@dataclass(frozen=True, slots=True)
class OutboxPublishDecision:
    """Decision after a publish attempt against a claimed outbox row."""

    target_status: OutboxStatus
    clear_owner: bool
    clear_lease: bool
    schedule_retry: bool
    duplicate_publication_permitted: bool
    reason: str


def is_outbox_claimable(row: OutboxRecordSnapshot, *, now: datetime) -> bool:
    """Return True when a relay replica may claim the row."""
    return row.status is OutboxStatus.PENDING or (
        row.status is OutboxStatus.PROCESSING
        and is_lease_expired(row.processing_until, now=now)
    )


def decide_stale_outbox_recovery(
    row: OutboxRecordSnapshot,
    *,
    now: datetime,
) -> OutboxRecoveryDecision | None:
    """Recover expired processing leases back to pending."""
    if row.status is not OutboxStatus.PROCESSING:
        return None
    if not is_lease_expired(row.processing_until, now=now):
        return None
    return OutboxRecoveryDecision(
        target_status=OutboxStatus.PENDING,
        clear_owner=True,
        clear_lease=True,
        reason="stale_processing_lease",
    )


def decide_outbox_publish_result(
    row: OutboxRecordSnapshot,
    *,
    broker_ack_received: bool,
    classification: RetryClassification | None = None,
) -> OutboxPublishDecision:
    """Transition a claimed outbox row after a publish attempt."""
    if broker_ack_received:
        return OutboxPublishDecision(
            target_status=OutboxStatus.PUBLISHED,
            clear_owner=True,
            clear_lease=True,
            schedule_retry=False,
            duplicate_publication_permitted=True,
            reason="jetstream_publish_ack",
        )

    if classification is None:
        classification = RetryClassification.TRANSIENT

    if should_quarantine(
        classification=classification,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
    ):
        return OutboxPublishDecision(
            target_status=OutboxStatus.QUARANTINED,
            clear_owner=True,
            clear_lease=True,
            schedule_retry=False,
            duplicate_publication_permitted=True,
            reason="publish_quarantined",
        )

    return OutboxPublishDecision(
        target_status=OutboxStatus.PENDING,
        clear_owner=True,
        clear_lease=True,
        schedule_retry=True,
        duplicate_publication_permitted=True,
        reason="publish_retryable",
    )


def transport_msg_id_for_outbox(row: OutboxRecordSnapshot) -> str:
    """Suggested bounded-transport dedupe key — not business idempotency authority."""
    return str(row.event_id)
