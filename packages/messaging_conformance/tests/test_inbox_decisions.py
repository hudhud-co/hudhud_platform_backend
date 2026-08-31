"""Tests for inbox duplicate and delivery decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from messaging_conformance import (
    InboxRecordSnapshot,
    InboxStatus,
    InboxUniqueKey,
    JetStreamConsumerAction,
    QuarantineRedeliveryPolicy,
    decide_handler_rollback_action,
    decide_inbox_duplicate_delivery,
    decide_post_commit_jetstream_action,
)


def test_processed_duplicate_acks_without_rerun() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.PROCESSED,
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert decision.jetstream_action is JetStreamConsumerAction.ACK
    assert decision.rerun_handler is False


def test_quarantined_duplicate_uses_terminal_policy() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.QUARANTINED,
    )
    terminal = decide_inbox_duplicate_delivery(
        existing,
        now=now,
        quarantine_policy=QuarantineRedeliveryPolicy.ACK_TERMINAL,
    )
    assert terminal.jetstream_action is JetStreamConsumerAction.ACK

    replay = decide_inbox_duplicate_delivery(
        existing,
        now=now,
        quarantine_policy=QuarantineRedeliveryPolicy.REPLAY_RESET,
    )
    assert replay.jetstream_action is JetStreamConsumerAction.NAK
    assert replay.rerun_handler is True


def test_failed_duplicate_naks_for_retry() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.FAILED,
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert decision.jetstream_action is JetStreamConsumerAction.NAK
    assert decision.rerun_handler is True


def test_active_processing_lease_defers_without_second_effect() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.PROCESSING,
        processing_lease_until=now + timedelta(seconds=30),
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert decision.jetstream_action is JetStreamConsumerAction.DEFER
    assert decision.rerun_handler is False


def test_expired_processing_lease_is_reclaimable() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.PROCESSING,
        processing_lease_until=now - timedelta(seconds=1),
    )
    decision = decide_inbox_duplicate_delivery(existing, now=now)
    assert decision.jetstream_action is JetStreamConsumerAction.NAK
    assert decision.rerun_handler is True


def test_post_commit_processed_resolves_redelivery_to_ack() -> None:
    now = datetime.now(tz=UTC)
    existing = InboxRecordSnapshot(
        key=InboxUniqueKey("consumer-a", uuid4()),
        status=InboxStatus.PROCESSED,
    )
    duplicate = decide_inbox_duplicate_delivery(existing, now=now)
    action = decide_post_commit_jetstream_action(
        committed_status=InboxStatus.PROCESSED,
        duplicate_decision=duplicate,
    )
    assert action is JetStreamConsumerAction.ACK


def test_handler_rollback_before_commit_naks_when_retryable() -> None:
    assert decide_handler_rollback_action(retryable=True) is JetStreamConsumerAction.NAK
    assert decide_handler_rollback_action(retryable=False) is JetStreamConsumerAction.ACK
