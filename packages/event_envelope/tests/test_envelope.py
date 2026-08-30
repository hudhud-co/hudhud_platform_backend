"""Envelope model validation tests."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from helpers import (
    AGGREGATE_ID,
    CORRELATION_ID,
    EVENT_ID,
    OCCURRED_AT,
    aggregate_command,
    aggregate_integration_event,
    non_aggregate_platform_message,
)

from event_envelope import (
    AggregateScope,
    DataClassification,
    EnvelopeValidationError,
    EventEnvelope,
    MessageKind,
)


def test_aggregate_command_valid() -> None:
    envelope = aggregate_command()
    assert envelope.message_kind == MessageKind.COMMAND
    assert envelope.aggregate_scope == AggregateScope.AGGREGATE
    assert envelope.aggregate_version == 7


def test_aggregate_integration_event_valid() -> None:
    envelope = aggregate_integration_event()
    assert envelope.message_kind == MessageKind.INTEGRATION
    assert envelope.aggregate_type == "shipment"


def test_non_aggregate_platform_message_valid() -> None:
    envelope = non_aggregate_platform_message()
    assert envelope.aggregate_scope == AggregateScope.NON_AGGREGATE
    assert envelope.aggregate_type is None
    assert envelope.aggregate_id is None


def test_missing_aggregate_identity_rejected() -> None:
    with pytest.raises(EnvelopeValidationError, match="aggregate_id"):
        EventEnvelope(
            event_id=EVENT_ID,
            event_type="pickup.fact.accepted",
            event_version=1,
            occurred_at=OCCURRED_AT,
            producer="pickup",
            message_kind=MessageKind.INTEGRATION,
            aggregate_scope=AggregateScope.AGGREGATE,
            aggregate_type="shipment",
            aggregate_id=None,
            aggregate_version=1,
            correlation_id=CORRELATION_ID,
            data_classification=DataClassification.INTERNAL,
            pii_present=False,
            payload={},
        )


def test_required_aggregate_version_for_ordering_kinds() -> None:
    with pytest.raises(EnvelopeValidationError, match="aggregate_version"):
        EventEnvelope(
            event_id=EVENT_ID,
            event_type="pickup.fact.accepted",
            event_version=1,
            occurred_at=OCCURRED_AT,
            producer="pickup",
            message_kind=MessageKind.INTEGRATION,
            aggregate_scope=AggregateScope.AGGREGATE,
            aggregate_type="shipment",
            aggregate_id=AGGREGATE_ID,
            aggregate_version=None,
            correlation_id=CORRELATION_ID,
            data_classification=DataClassification.INTERNAL,
            pii_present=False,
            payload={},
        )


def test_reply_without_aggregate_version_allowed() -> None:
    envelope = EventEnvelope(
        event_id=EVENT_ID,
        event_type="shipment.result.lifecycle_updated",
        event_version=1,
        occurred_at=OCCURRED_AT,
        producer="shipment",
        message_kind=MessageKind.REPLY,
        aggregate_scope=AggregateScope.AGGREGATE,
        aggregate_type="shipment",
        aggregate_id=AGGREGATE_ID,
        aggregate_version=None,
        correlation_id=CORRELATION_ID,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        payload={"applied": True},
    )
    assert envelope.aggregate_version is None


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(EnvelopeValidationError, match="timezone-aware"):
        EventEnvelope(
            event_id=EVENT_ID,
            event_type="pickup.fact.accepted",
            event_version=1,
            occurred_at=datetime(2026, 8, 30, 11, 15, 42),
            producer="pickup",
            message_kind=MessageKind.INTEGRATION,
            aggregate_scope=AggregateScope.AGGREGATE,
            aggregate_type="shipment",
            aggregate_id=AGGREGATE_ID,
            aggregate_version=1,
            correlation_id=CORRELATION_ID,
            data_classification=DataClassification.INTERNAL,
            pii_present=False,
            payload={},
        )


def test_non_aggregate_must_not_carry_aggregate_fields() -> None:
    with pytest.raises(EnvelopeValidationError, match="aggregate_type"):
        EventEnvelope(
            event_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            event_type="platform.health.ping",
            event_version=1,
            occurred_at=OCCURRED_AT,
            producer="gateway",
            message_kind=MessageKind.PROJECTION,
            aggregate_scope=AggregateScope.NON_AGGREGATE,
            aggregate_type="shipment",
            correlation_id=CORRELATION_ID,
            data_classification=DataClassification.PUBLIC,
            pii_present=False,
            payload={},
        )


def test_sensitive_repr_redacts_payload() -> None:
    envelope = aggregate_command()
    text = repr(envelope)
    assert "task_id" not in text
    assert "<redacted>" in text


def test_safe_log_fields_exclude_payload() -> None:
    envelope = aggregate_command()
    fields = envelope.safe_log_fields()
    assert "payload" not in fields
    assert fields["event_type"] == "delivery.command.complete"
