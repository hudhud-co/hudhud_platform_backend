"""W3C trace context validation tests."""

from __future__ import annotations

import pytest
from helpers import load_example

from event_envelope import EnvelopeSerdePolicy, EnvelopeValidationError, deserialize_envelope
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


def test_ignore_policy_drops_invalid_on_deserialize() -> None:
    data = load_example("aggregate_command.json")
    data["traceparent"] = "invalid"
    policy = EnvelopeSerdePolicy(trace_context_policy=TraceContextPolicy.NORMALIZE)
    envelope = deserialize_envelope(data, policy=policy)
    assert envelope.traceparent is None
