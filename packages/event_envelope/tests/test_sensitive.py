"""Sensitive error and limit behavior tests."""

from __future__ import annotations

import pytest
from helpers import aggregate_command

from event_envelope import (
    EnvelopeLimits,
    EnvelopeSerdePolicy,
    EnvelopeValidationError,
    serialize_envelope,
)


def test_validation_error_repr_is_safe() -> None:
    error = EnvelopeValidationError("payload", "invalid structure")
    text = repr(error)
    assert "secret" not in text
    assert "payload" in text


def test_envelope_size_limit_configurable() -> None:
    envelope = aggregate_command()
    tiny_limits = EnvelopeLimits(hard_envelope_bytes=10)
    policy = EnvelopeSerdePolicy(limits=tiny_limits)
    with pytest.raises(EnvelopeValidationError, match="envelope_size"):
        serialize_envelope(envelope, policy=policy)


def test_default_limits_not_frozen_architectural_constant() -> None:
    limits = EnvelopeLimits()
    assert limits.hard_envelope_bytes == 256 * 1024
    custom = EnvelopeLimits(hard_envelope_bytes=512 * 1024)
    assert custom.hard_envelope_bytes != limits.hard_envelope_bytes
