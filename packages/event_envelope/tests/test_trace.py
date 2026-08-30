"""W3C trace context validation tests."""

from __future__ import annotations

import pytest
from helpers import (
    AGGREGATE_ID,
    CORRELATION_ID,
    EVENT_ID,
    OCCURRED_AT,
    load_example,
)

from event_envelope import (
    AggregateScope,
    DataClassification,
    EnvelopeSerdePolicy,
    EnvelopeValidationError,
    EventEnvelope,
    MessageKind,
    deserialize_envelope,
)
from event_envelope.trace import (
    TraceContextPolicy,
    apply_trace_context_policy,
    normalize_traceparent,
    validate_traceparent,
)


def test_valid_traceparent_normalized_to_lowercase() -> None:
    value = "00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01"
    assert (
        validate_traceparent(value)
        == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )


def test_invalid_traceparent_rejected() -> None:
    with pytest.raises(EnvelopeValidationError, match="traceparent"):
        validate_traceparent("not-a-traceparent")


def test_all_zero_trace_id_rejected() -> None:
    assert normalize_traceparent("00-" + "0" * 32 + "-" + "a" * 16 + "-01") is None


def test_normalize_policy_strips_invalid() -> None:
    assert apply_trace_context_policy("bad", policy=TraceContextPolicy.NORMALIZE) is None


def test_reject_policy_raises() -> None:
    with pytest.raises(EnvelopeValidationError):
        apply_trace_context_policy("bad", policy=TraceContextPolicy.REJECT)


def test_producer_model_build_rejects_invalid_traceparent() -> None:
    with pytest.raises(EnvelopeValidationError, match="traceparent"):
        EventEnvelope(
            event_id=EVENT_ID,
            event_type="delivery.command.complete",
            event_version=1,
            occurred_at=OCCURRED_AT,
            producer="delivery",
            message_kind=MessageKind.COMMAND,
            aggregate_scope=AggregateScope.AGGREGATE,
            aggregate_type="delivery_task",
            aggregate_id=AGGREGATE_ID,
            aggregate_version=1,
            correlation_id=CORRELATION_ID,
            traceparent="invalid",
            data_classification=DataClassification.INTERNAL,
            pii_present=False,
            payload={},
        )


def test_ignore_policy_drops_invalid_on_deserialize() -> None:
    data = load_example("aggregate_command.json")
    data["traceparent"] = "invalid"
    policy = EnvelopeSerdePolicy(trace_context_policy=TraceContextPolicy.NORMALIZE)
    envelope = deserialize_envelope(data, policy=policy)
    assert envelope.traceparent is None
