"""Durable idempotent inbox for events Workforce consumes (ADR-0008).

JetStream is at-least-once, so the same message will arrive twice. Deduplication lives
here rather than in the broker: the decision of what a redelivery *means* depends on how
far the previous attempt got, which only this service knows.

The decision table itself is the shared, pure one in `messaging_conformance`, so every
service in the platform answers a redelivery the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from messaging_conformance.enums import (
    InboxStatus as SharedInboxStatus,
)
from messaging_conformance.enums import (
    JetStreamConsumerAction,
    QuarantineRedeliveryPolicy,
)
from messaging_conformance.inbox_decisions import (
    decide_inbox_duplicate_delivery,
    decide_post_commit_jetstream_action,
)
from messaging_conformance.values import InboxRecordSnapshot, InboxUniqueKey

from workforce.domain.messaging import InboxRecord, InboxStatus
from workforce.ports.repository import WorkforceUnitOfWork

DEFAULT_LEASE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What the consumer should tell JetStream, and whether the handler actually ran."""

    action: JetStreamConsumerAction
    handler_ran: bool
    reason: str
    record: InboxRecord | None = None


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InboxService:
    def __init__(
        self,
        unit_of_work: WorkforceUnitOfWork,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        quarantine_policy: QuarantineRedeliveryPolicy = (
            QuarantineRedeliveryPolicy.ACK_TERMINAL
        ),
    ) -> None:
        self._uow = unit_of_work
        self._lease_seconds = lease_seconds
        self._quarantine_policy = quarantine_policy

    def handle(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        event_type: str,
        event_version: int,
        payload: dict[str, Any],
        handler: Callable[[dict[str, Any]], None],
    ) -> DeliveryOutcome:
        """Run ``handler`` at most once per ``(consumer_name, event_id)``.

        A second delivery while the first is still inside its lease is deferred rather
        than run: that is the case where running again would produce a second effect.
        """
        self._uow.begin()
        try:
            existing = self._uow.inbox.find(consumer_name, event_id)
            if existing is not None:
                outcome = self._handle_duplicate(
                    existing, payload=payload, handler=handler
                )
                self._uow.commit()
                return outcome

            moment = _now()
            record = InboxRecord(
                inbox_id=uuid4(),
                consumer_name=consumer_name,
                event_id=event_id,
                event_type=event_type,
                event_version=event_version,
                status=InboxStatus.PROCESSING,
                received_at=moment,
                attempt_count=1,
                processing_started_at=moment,
                processing_lease_until=moment + timedelta(seconds=self._lease_seconds),
                payload_json=payload,
            )
            self._uow.inbox.insert(record)
            outcome = self._run(record, payload=payload, handler=handler)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return outcome

    # ------------------------------------------------------------- internals

    def _handle_duplicate(
        self,
        existing: InboxRecord,
        *,
        payload: dict[str, Any],
        handler: Callable[[dict[str, Any]], None],
    ) -> DeliveryOutcome:
        decision = decide_inbox_duplicate_delivery(
            InboxRecordSnapshot(
                key=InboxUniqueKey(
                    consumer_name=existing.consumer_name, event_id=existing.event_id
                ),
                status=SharedInboxStatus(existing.status.value),
                processing_started_at=existing.processing_started_at,
                processing_lease_until=existing.processing_lease_until,
                attempt_count=existing.attempt_count,
            ),
            now=_now(),
            quarantine_policy=self._quarantine_policy,
        )
        if not decision.rerun_handler:
            return DeliveryOutcome(
                action=decision.jetstream_action,
                handler_ran=False,
                reason=decision.reason,
                record=existing,
            )

        moment = _now()
        existing.status = InboxStatus.PROCESSING
        existing.attempt_count += 1
        existing.processing_started_at = moment
        existing.processing_lease_until = moment + timedelta(seconds=self._lease_seconds)
        self._uow.inbox.save(existing)
        return self._run(
            existing, payload=payload, handler=handler, duplicate_reason=decision.reason
        )

    def _run(
        self,
        record: InboxRecord,
        *,
        payload: dict[str, Any],
        handler: Callable[[dict[str, Any]], None],
        duplicate_reason: str | None = None,
    ) -> DeliveryOutcome:
        try:
            handler(payload)
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed silently
            record.status = InboxStatus.FAILED
            record.processing_lease_until = None
            record.last_error_code = type(exc).__name__
            self._uow.inbox.save(record)
            return DeliveryOutcome(
                action=JetStreamConsumerAction.NAK,
                handler_ran=True,
                reason=duplicate_reason or "handler_failed",
                record=record,
            )

        record.status = InboxStatus.PROCESSED
        record.processed_at = _now()
        record.processing_lease_until = None
        record.last_error_code = None
        self._uow.inbox.save(record)
        action = decide_post_commit_jetstream_action(
            committed_status=SharedInboxStatus.PROCESSED
        )
        return DeliveryOutcome(
            action=action,
            handler_ran=True,
            reason=duplicate_reason or "processed",
            record=record,
        )
