"""Conformance assertion helpers for service adapter tests."""

from __future__ import annotations

import re

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction, OutboxStatus
from messaging_conformance.inbox_decisions import InboxDuplicateDecision
from messaging_conformance.outbox_decisions import OutboxPublishDecision

FORBIDDEN_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\+?\d{10,15}"),  # phone-like
    re.compile(r"sk_(live|test)_[A-Za-z0-9]+"),  # API key-like
)


def assert_jetstream_action(
    actual: JetStreamConsumerAction,
    expected: JetStreamConsumerAction,
    *,
    context: str,
) -> None:
    assert actual is expected, f"{context}: expected {expected}, got {actual}"


def assert_no_handler_rerun(decision: InboxDuplicateDecision, *, context: str) -> None:
    assert not decision.rerun_handler, f"{context}: handler must not rerun"


def assert_handler_rerun(decision: InboxDuplicateDecision, *, context: str) -> None:
    assert decision.rerun_handler, f"{context}: handler must rerun"


def assert_outbox_terminal(status: OutboxStatus, *, context: str) -> None:
    assert status in {OutboxStatus.PUBLISHED, OutboxStatus.QUARANTINED}, (
        f"{context}: unexpected outbox status {status}"
    )


def assert_inbox_terminal(status: InboxStatus, *, context: str) -> None:
    assert status in {InboxStatus.PROCESSED, InboxStatus.QUARANTINED}, (
        f"{context}: unexpected inbox status {status}"
    )


def assert_publish_ack_transition(decision: OutboxPublishDecision, *, context: str) -> None:
    assert decision.target_status is OutboxStatus.PUBLISHED, context
    assert decision.clear_owner and decision.clear_lease, context


def assert_sanitized_error_message(message: str, *, context: str) -> None:
    for pattern in FORBIDDEN_ERROR_PATTERNS:
        assert not pattern.search(message), f"{context}: forbidden pattern in error message"
