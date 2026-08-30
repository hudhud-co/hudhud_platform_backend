"""Serialization, compatibility, and unknown-field policy tests."""

from __future__ import annotations

import json

import pytest
from helpers import (
    aggregate_command,
    aggregate_integration_event,
    load_example,
    non_aggregate_platform_message,
)

from event_envelope import (
    EnvelopeSerdePolicy,
    EnvelopeValidationError,
    UnknownFieldPolicy,
    UnsupportedEnvelopeVersionError,
    deserialize_envelope,
    serialize_envelope,
)
from event_envelope.compatibility import EnvelopeCompatibility


def test_deterministic_round_trip() -> None:
    envelope = aggregate_integration_event()
    first = serialize_envelope(envelope)
    second = serialize_envelope(deserialize_envelope(first))
    assert first == second
    assert '"occurred_at":"2026-08-30T11:15:42.456Z"' in first
    assert "7c9e6679-7425-40de-944b-e07fc1f90ae7" in first


def test_unsupported_envelope_version_rejected() -> None:
    data = load_example("aggregate_command.json")
    data["envelope_version"] = 99
    with pytest.raises(UnsupportedEnvelopeVersionError):
        deserialize_envelope(data)


def test_unknown_fields_ignored_by_default() -> None:
    data = load_example("aggregate_command.json")
    data["future_field"] = "additive"
    envelope = deserialize_envelope(data)
    assert envelope.event_type == "delivery.command.complete"


def test_unknown_fields_rejected_in_strict_mode() -> None:
    data = load_example("aggregate_command.json")
    data["future_field"] = "additive"
    policy = EnvelopeSerdePolicy(unknown_field_policy=UnknownFieldPolicy.REJECT)
    with pytest.raises(EnvelopeValidationError, match="unknown fields"):
        deserialize_envelope(data, policy=policy)


def test_unknown_fields_preserved_when_configured() -> None:
    data = load_example("aggregate_command.json")
    data["future_field"] = "additive"
    policy = EnvelopeSerdePolicy(unknown_field_policy=UnknownFieldPolicy.PRESERVE)
    envelope = deserialize_envelope(data, policy=policy)
    roundtrip = json.loads(serialize_envelope(envelope))
    assert roundtrip["future_field"] == "additive"


def test_compatibility_additive_vs_breaking() -> None:
    policy = EnvelopeCompatibility()
    assert policy.is_additive_change(added_fields=frozenset({"future_field"}))
    assert policy.requires_envelope_version_bump(removed_fields=frozenset({"producer"}))


def test_example_fixtures_round_trip() -> None:
    for name in (
        "aggregate_command.json",
        "aggregate_integration_event.json",
        "non_aggregate_platform.json",
    ):
        raw = load_example(name)
        envelope = deserialize_envelope(raw)
        assert envelope.envelope_version == 1


def test_non_aggregate_example_round_trip() -> None:
    envelope = non_aggregate_platform_message()
    restored = deserialize_envelope(serialize_envelope(envelope))
    assert restored.aggregate_scope.value == "non_aggregate"


def test_command_example_round_trip() -> None:
    envelope = aggregate_command()
    restored = deserialize_envelope(serialize_envelope(envelope))
    assert restored.message_kind.value == "command"
    assert restored.aggregate_version == 7
