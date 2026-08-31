"""Transport-independent A2 observation consumer coordinator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_OID, UUID, uuid5

from event_envelope.errors import EnvelopeValidationError, UnsupportedEnvelopeVersionError
from event_envelope.serde import CONSUMER_SERDE_POLICY, deserialize_envelope
from messaging_conformance.enums import (
    InboxStatus,
    JetStreamConsumerAction,
    QuarantineRedeliveryPolicy,
    RetryClassification,
)
from messaging_conformance.inbox_decisions import (
    decide_handler_rollback_action,
    decide_inbox_duplicate_delivery,
    decide_post_commit_jetstream_action,
)
from messaging_conformance.retry import classify_retry_error, should_quarantine

from audit.application.validation import project_observation, validate_a2_delivery
from audit.domain.contract import A2_EVENT_TYPE, A2_EVENT_VERSION
from audit.domain.errors import ContractRejection, PoisonHandlerError, RetryableHandlerError
from audit.domain.sanitize import sanitize_error_message
from audit.domain.types import Delivery, InboxRow, ValidatedA2Message
from audit.ports import (
    ConsumerTransportPort,
    HandleOutcome,
    InboxStorePort,
    ObservationStorePort,
    UnitOfWorkPort,
)

logger = logging.getLogger("audit.observation_consumer")


class ObservationConsumerCoordinator:
    """JetStream delivery → validate → inbox → projection → commit → ACK."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkPort,
        inbox: InboxStorePort,
        observations: ObservationStorePort,
        transport: ConsumerTransportPort,
        consumer_name: str,
        handler_version: str,
        processing_owner: str,
        lease_duration: timedelta,
        max_attempts: int,
        clock: Callable[[], datetime] | None = None,
        quarantine_policy: QuarantineRedeliveryPolicy = QuarantineRedeliveryPolicy.ACK_TERMINAL,
    ) -> None:
        self._uow = unit_of_work
        self._inbox = inbox
        self._observations = observations
        self._transport = transport
        self._consumer_name = consumer_name
        self._handler_version = handler_version
        self._processing_owner = processing_owner
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))
        self._quarantine_policy = quarantine_policy

    def handle(self, delivery: Delivery) -> HandleOutcome:
        now = self._clock()
        envelope = self._deserialize(delivery)
        if envelope is None:
            return self._quarantine_deserialize_poison(delivery, now)

        try:
            validated = validate_a2_delivery(envelope=envelope, delivery=delivery)
        except ContractRejection as exc:
            return self._quarantine_permanent(
                delivery,
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                event_version=envelope.event_version,
                correlation_id=envelope.correlation_id,
                now=now,
                error_code=exc.code,
                error_message=exc.detail,
            )

        self._log_safe("audit_observation_received", extra={"event_id": str(validated.event_id)})
        return self._process_validated(delivery, validated, now)

    def _deserialize(self, delivery: Delivery):
        try:
            return deserialize_envelope(delivery.body, policy=CONSUMER_SERDE_POLICY)
        except (
            EnvelopeValidationError,
            UnsupportedEnvelopeVersionError,
            ValueError,
            TypeError,
            UnicodeDecodeError,
        ) as exc:
            self._log_safe(
                "audit_observation_deserialize_failed",
                extra={
                    "error_code": "DESERIALIZE_FAILURE",
                    "error": sanitize_error_message(str(exc)),
                },
            )
            return None

    def _quarantine_deserialize_poison(self, delivery: Delivery, now: datetime) -> HandleOutcome:
        event_id = _poison_delivery_event_id(delivery)
        return self._quarantine_permanent(
            delivery,
            event_id=event_id,
            event_type=A2_EVENT_TYPE,
            event_version=A2_EVENT_VERSION,
            correlation_id=None,
            now=now,
            error_code="DESERIALIZE_FAILURE",
            error_message="envelope could not be deserialized",
        )

    def _process_validated(
        self,
        delivery: Delivery,
        validated: ValidatedA2Message,
        now: datetime,
    ) -> HandleOutcome:
        self._uow.begin()
        try:
            inserted = self._inbox.try_insert_received(
                consumer_name=self._consumer_name,
                event_id=validated.event_id,
                event_type=validated.event_type,
                event_version=validated.event_version,
                handler_version=self._handler_version,
                processing_owner=self._processing_owner,
                processing_lease_until=now + self._lease_duration,
                received_at=now,
                correlation_id=validated.correlation_id,
                jetstream_stream=delivery.stream,
                jetstream_seq=delivery.jetstream_seq,
                nats_msg_id=delivery.nats_msg_id,
            )
            if inserted is None:
                return self._handle_duplicate(delivery, validated, now)
            return self._apply_new_delivery(delivery, validated, inserted, now)
        except RetryableHandlerError as exc:
            self._uow.rollback()
            action = decide_handler_rollback_action(retryable=True)
            self._apply_transport(action, delivery)
            self._log_safe(
                "audit_observation_retryable_rollback",
                extra={"error_code": exc.code, "error": sanitize_error_message(exc.detail)},
            )
            return HandleOutcome(
                jetstream_action=action,
                inbox_status=None,
                observation_written=False,
                reason="retryable_rollback_nak",
            )
        except PoisonHandlerError as exc:
            self._uow.rollback()
            return self._quarantine_permanent(
                delivery,
                event_id=validated.event_id,
                event_type=validated.event_type,
                event_version=validated.event_version,
                correlation_id=validated.correlation_id,
                now=now,
                error_code=exc.code,
                error_message=exc.detail,
            )

    def _apply_new_delivery(
        self,
        delivery: Delivery,
        validated: ValidatedA2Message,
        _inserted: InboxRow,
        now: datetime,
    ) -> HandleOutcome:
        written = self._project(validated, received_at=now)
        self._inbox.mark_processed(
            consumer_name=self._consumer_name,
            event_id=validated.event_id,
            processed_at=now,
        )
        self._uow.commit()
        action = decide_post_commit_jetstream_action(committed_status=InboxStatus.PROCESSED)
        self._apply_transport(action, delivery)
        return HandleOutcome(
            jetstream_action=action,
            inbox_status=InboxStatus.PROCESSED,
            observation_written=written,
            reason="processed_ack_after_commit",
        )

    def _handle_duplicate(
        self,
        delivery: Delivery,
        validated: ValidatedA2Message,
        now: datetime,
    ) -> HandleOutcome:
        existing = self._inbox.load_existing(
            consumer_name=self._consumer_name,
            event_id=validated.event_id,
        )
        if existing is None:
            raise RetryableHandlerError(
                "DB_SERIALIZATION_FAILURE",
                "inbox conflict without existing row",
            )
        decision = decide_inbox_duplicate_delivery(
            existing.snapshot(),
            now=now,
            quarantine_policy=self._quarantine_policy,
        )
        if not decision.rerun_handler:
            self._uow.rollback()
            self._apply_transport(decision.jetstream_action, delivery)
            return HandleOutcome(
                jetstream_action=decision.jetstream_action,
                inbox_status=existing.status,
                observation_written=False,
                reason=decision.reason,
            )

        next_attempts = existing.attempt_count + 1
        if should_quarantine(
            classification=RetryClassification.TRANSIENT,
            attempt_count=next_attempts,
            max_attempts=self._max_attempts,
        ):
            row = self._inbox.mark_quarantined(
                consumer_name=self._consumer_name,
                event_id=validated.event_id,
                quarantined_at=now,
                error_code="MAX_DELIVER_EXCEEDED",
                error_message="inbox attempts exhausted",
            )
            self._uow.commit()
            action = decide_post_commit_jetstream_action(committed_status=InboxStatus.QUARANTINED)
            self._apply_transport(action, delivery)
            return HandleOutcome(
                jetstream_action=action,
                inbox_status=row.status,
                observation_written=False,
                reason="max_attempts_quarantine_ack",
            )

        self._inbox.reclaim_processing(
            consumer_name=self._consumer_name,
            event_id=validated.event_id,
            processing_owner=self._processing_owner,
            processing_lease_until=now + self._lease_duration,
            now=now,
        )
        written = self._project(validated, received_at=now)
        self._inbox.mark_processed(
            consumer_name=self._consumer_name,
            event_id=validated.event_id,
            processed_at=now,
        )
        self._uow.commit()
        action = decide_post_commit_jetstream_action(committed_status=InboxStatus.PROCESSED)
        self._apply_transport(action, delivery)
        return HandleOutcome(
            jetstream_action=action,
            inbox_status=InboxStatus.PROCESSED,
            observation_written=written,
            reason="reclaimed_processed_ack_after_commit",
        )

    def _quarantine_permanent(
        self,
        delivery: Delivery,
        *,
        event_id: UUID,
        event_type: str,
        event_version: int,
        correlation_id: UUID | None,
        now: datetime,
        error_code: str,
        error_message: str,
    ) -> HandleOutcome:
        classification = classify_retry_error(error_code)
        sanitized = sanitize_error_message(error_message)
        self._uow.begin()
        try:
            inserted = self._inbox.try_insert_received(
                consumer_name=self._consumer_name,
                event_id=event_id,
                event_type=event_type,
                event_version=event_version,
                handler_version=self._handler_version,
                processing_owner=self._processing_owner,
                processing_lease_until=now + self._lease_duration,
                received_at=now,
                correlation_id=correlation_id,
                jetstream_stream=delivery.stream,
                jetstream_seq=delivery.jetstream_seq,
                nats_msg_id=delivery.nats_msg_id,
            )
            if inserted is None:
                existing = self._inbox.load_existing(
                    consumer_name=self._consumer_name,
                    event_id=event_id,
                )
                if existing is not None and existing.status is InboxStatus.PROCESSED:
                    self._uow.rollback()
                    self._transport.ack(delivery)
                    return HandleOutcome(
                        jetstream_action=JetStreamConsumerAction.ACK,
                        inbox_status=InboxStatus.PROCESSED,
                        observation_written=False,
                        reason="processed_duplicate_on_reject",
                    )
            row = self._inbox.mark_quarantined(
                consumer_name=self._consumer_name,
                event_id=event_id,
                quarantined_at=now,
                error_code=error_code,
                error_message=sanitized,
            )
            self._uow.commit()
        except RetryableHandlerError as exc:
            self._uow.rollback()
            action = decide_handler_rollback_action(retryable=True)
            self._apply_transport(action, delivery)
            self._log_safe(
                "audit_observation_quarantine_persistence_failed",
                extra={"error_code": exc.code, "error": sanitize_error_message(exc.detail)},
            )
            return HandleOutcome(
                jetstream_action=action,
                inbox_status=None,
                observation_written=False,
                reason="quarantine_persistence_failure_nak",
            )
        action = decide_post_commit_jetstream_action(committed_status=InboxStatus.QUARANTINED)
        self._apply_transport(action, delivery)
        self._log_safe(
            "audit_observation_quarantined",
            extra={"error_code": error_code, "classification": classification.value},
        )
        return HandleOutcome(
            jetstream_action=action,
            inbox_status=row.status,
            observation_written=False,
            reason="permanent_quarantine_ack",
        )

    def _project(self, validated: ValidatedA2Message, *, received_at: datetime) -> bool:
        observation = project_observation(validated, received_at=received_at)
        return self._observations.insert_if_absent(observation)

    def _apply_transport(self, action: JetStreamConsumerAction, delivery: Delivery) -> None:
        if action is JetStreamConsumerAction.ACK:
            self._transport.ack(delivery)
        elif action is JetStreamConsumerAction.NAK:
            self._transport.nak(delivery)
        else:
            self._transport.defer(delivery)

    def _log_safe(self, message: str, *, extra: dict[str, str]) -> None:
        logger.info(message, extra=extra)


def _poison_delivery_event_id(delivery: Delivery) -> UUID:
    if delivery.nats_msg_id:
        try:
            return UUID(delivery.nats_msg_id)
        except ValueError:
            pass
    digest = sha256(
        b"|".join(
            [
                delivery.consumer_name.encode(),
                delivery.subject.encode(),
                str(delivery.jetstream_seq or "").encode(),
                delivery.body[:256],
            ]
        )
    ).hexdigest()
    return uuid5(NAMESPACE_OID, f"audit-poison:{digest}")
