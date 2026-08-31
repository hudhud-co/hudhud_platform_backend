"""Pure inbox duplicate and delivery decision functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from messaging_conformance.enums import (
    InboxStatus,
    JetStreamConsumerAction,
    QuarantineRedeliveryPolicy,
)
from messaging_conformance.lease import is_lease_active, is_lease_expired
from messaging_conformance.values import InboxRecordSnapshot


@dataclass(frozen=True, slots=True)
class InboxDuplicateDecision:
    """Outcome for a duplicate `(consumer_name, event_id)` delivery."""

    jetstream_action: JetStreamConsumerAction
    rerun_handler: bool
    reason: str


@dataclass(frozen=True, slots=True)
class InboxInsertDecision:
    """Outcome when the first inbox insert succeeds in a handler transaction."""

    proceed_with_handler: bool
    initial_status: InboxStatus


def decide_inbox_duplicate_delivery(
    existing: InboxRecordSnapshot,
    *,
    now: datetime,
    quarantine_policy: QuarantineRedeliveryPolicy = QuarantineRedeliveryPolicy.ACK_TERMINAL,
) -> InboxDuplicateDecision:
    """Apply ADR-0008 state-aware duplicate handling."""
    status = existing.status

    if status is InboxStatus.PROCESSED:
        return InboxDuplicateDecision(
            jetstream_action=JetStreamConsumerAction.ACK,
            rerun_handler=False,
            reason="terminal_processed_duplicate",
        )

    if status is InboxStatus.QUARANTINED:
        if quarantine_policy is QuarantineRedeliveryPolicy.REPLAY_RESET:
            return InboxDuplicateDecision(
                jetstream_action=JetStreamConsumerAction.NAK,
                rerun_handler=True,
                reason="quarantined_replay_reset",
            )
        return InboxDuplicateDecision(
            jetstream_action=JetStreamConsumerAction.ACK,
            rerun_handler=False,
            reason="quarantined_terminal_duplicate",
        )

    if status is InboxStatus.FAILED:
        return InboxDuplicateDecision(
            jetstream_action=JetStreamConsumerAction.NAK,
            rerun_handler=True,
            reason="retryable_failed_duplicate",
        )

    if status is InboxStatus.PROCESSING:
        if is_lease_expired(existing.processing_lease_until, now=now):
            return InboxDuplicateDecision(
                jetstream_action=JetStreamConsumerAction.NAK,
                rerun_handler=True,
                reason="expired_processing_lease_reclaim",
            )
        if is_lease_active(existing.processing_lease_until, now=now):
            return InboxDuplicateDecision(
                jetstream_action=JetStreamConsumerAction.DEFER,
                rerun_handler=False,
                reason="active_processing_lease_no_second_effect",
            )
        return InboxDuplicateDecision(
            jetstream_action=JetStreamConsumerAction.NAK,
            rerun_handler=True,
            reason="processing_without_lease_reclaim",
        )

    if status is InboxStatus.RECEIVED:
        return InboxDuplicateDecision(
            jetstream_action=JetStreamConsumerAction.NAK,
            rerun_handler=True,
            reason="incomplete_received_redelivery",
        )

    msg = f"unsupported inbox status: {status}"
    raise ValueError(msg)


def decide_post_commit_jetstream_action(
    *,
    committed_status: InboxStatus,
    duplicate_decision: InboxDuplicateDecision | None = None,
) -> JetStreamConsumerAction:
    """Resolve JetStream action after a successful handler transaction."""
    if duplicate_decision is not None:
        return duplicate_decision.jetstream_action
    if committed_status is InboxStatus.PROCESSED:
        return JetStreamConsumerAction.ACK
    if committed_status is InboxStatus.QUARANTINED:
        return JetStreamConsumerAction.ACK
    if committed_status is InboxStatus.FAILED:
        return JetStreamConsumerAction.NAK
    return JetStreamConsumerAction.NAK


def decide_handler_rollback_action(*, retryable: bool) -> JetStreamConsumerAction:
    """JetStream action when handler transaction rolls back before commit."""
    if retryable:
        return JetStreamConsumerAction.NAK
    return JetStreamConsumerAction.ACK
