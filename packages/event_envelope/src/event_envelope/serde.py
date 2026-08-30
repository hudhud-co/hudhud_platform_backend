"""Deterministic envelope serialization helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.compatibility import (
    ENVELOPE_COMPATIBILITY_POLICY,
    EnvelopeCompatibility,
    UnknownFieldPolicy,
)
from event_envelope.envelope import EventEnvelope
from event_envelope.errors import EnvelopeValidationError
from event_envelope.limits import DEFAULT_ENVELOPE_LIMITS, EnvelopeLimits
from event_envelope.primitives import format_utc_datetime, format_uuid
from event_envelope.trace import TraceContextPolicy, apply_trace_context_policy


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return format_uuid(value)
    if isinstance(value, datetime):
        return format_utc_datetime(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError(type(value))


@dataclass(frozen=True, slots=True)
class EnvelopeSerdePolicy:
    """Serialization and deserialization policy."""

    compatibility: EnvelopeCompatibility = ENVELOPE_COMPATIBILITY_POLICY
    limits: EnvelopeLimits = DEFAULT_ENVELOPE_LIMITS
    unknown_field_policy: UnknownFieldPolicy = UnknownFieldPolicy.PRESERVE
    trace_context_policy: TraceContextPolicy = TraceContextPolicy.NORMALIZE

    def serialize(self, envelope: EventEnvelope) -> str:
        if (
            self.unknown_field_policy == UnknownFieldPolicy.REJECT
            and envelope.unknown_fields
        ):
            raise EnvelopeValidationError(
                "envelope",
                f"unknown fields not permitted: {', '.join(sorted(envelope.unknown_fields))}",
            )
        payload = envelope_to_json_dict(envelope)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
        self.limits.validate_envelope_size(len(encoded.encode("utf-8")))
        return encoded

    def deserialize(self, raw: str | bytes | dict[str, Any]) -> EventEnvelope:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
        return parse_envelope_json(data, policy=self)


# Role-aware defaults: producers are strict; consumers preserve or reject explicitly.
PRODUCER_SERDE_POLICY = EnvelopeSerdePolicy(
    unknown_field_policy=UnknownFieldPolicy.REJECT,
    trace_context_policy=TraceContextPolicy.REJECT,
)
CONSUMER_SERDE_POLICY = EnvelopeSerdePolicy(
    unknown_field_policy=UnknownFieldPolicy.PRESERVE,
    trace_context_policy=TraceContextPolicy.NORMALIZE,
)


def envelope_to_json_dict(envelope: EventEnvelope) -> dict[str, Any]:
    """Canonical JSON-ready dict with deterministic field formatting."""
    return envelope.model_dump_json_ready()


def envelope_to_json_str(
    envelope: EventEnvelope,
    *,
    policy: EnvelopeSerdePolicy | None = None,
) -> str:
    serde = policy or PRODUCER_SERDE_POLICY
    return serde.serialize(envelope)


def serialize_envelope(
    envelope: EventEnvelope,
    *,
    policy: EnvelopeSerdePolicy | None = None,
) -> str:
    """Serialize an envelope to deterministic JSON."""
    return envelope_to_json_str(envelope, policy=policy)


def parse_envelope_json(
    data: dict[str, Any],
    *,
    policy: EnvelopeSerdePolicy | None = None,
) -> EventEnvelope:
    """Parse a JSON object into a validated :class:`EventEnvelope`."""
    serde = policy or CONSUMER_SERDE_POLICY
    serde.compatibility.validate_envelope_version(int(data.get("envelope_version", 1)))

    unknown_keys = set(data) - EventEnvelope.known_field_names()
    if serde.unknown_field_policy == UnknownFieldPolicy.REJECT and unknown_keys:
        raise EnvelopeValidationError(
            "envelope",
            f"unknown fields not permitted: {', '.join(sorted(unknown_keys))}",
        )

    preserved: dict[str, Any] = {}
    if serde.unknown_field_policy == UnknownFieldPolicy.PRESERVE:
        preserved = {key: data[key] for key in unknown_keys}

    traceparent = data.get("traceparent")
    if traceparent is not None:
        data = dict(data)
        data["traceparent"] = apply_trace_context_policy(
            str(traceparent),
            policy=serde.trace_context_policy,
        )

    envelope = EventEnvelope.model_validate(data)
    if preserved:
        envelope = envelope.model_copy(update={"unknown_fields": preserved})
    return envelope


def deserialize_envelope(
    raw: str | bytes | dict[str, Any],
    *,
    policy: EnvelopeSerdePolicy | None = None,
) -> EventEnvelope:
    """Deserialize JSON into a validated envelope."""
    serde = policy or CONSUMER_SERDE_POLICY
    return serde.deserialize(raw)
