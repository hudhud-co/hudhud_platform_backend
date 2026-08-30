"""HUDHUD integration message envelope — public API."""

from event_envelope.compatibility import (
    ENVELOPE_COMPATIBILITY_POLICY,
    SUPPORTED_ENVELOPE_VERSION,
    EnvelopeCompatibility,
    UnknownFieldPolicy,
)
from event_envelope.enums import AggregateScope, DataClassification, MessageKind
from event_envelope.envelope import EnvelopeMetadata, EventEnvelope
from event_envelope.errors import (
    EnvelopeValidationError,
    UnsupportedEnvelopeVersionError,
)
from event_envelope.limits import DEFAULT_ENVELOPE_LIMITS, EnvelopeLimits
from event_envelope.media_refs import MediaRef
from event_envelope.serde import (
    CONSUMER_SERDE_POLICY,
    PRODUCER_SERDE_POLICY,
    EnvelopeSerdePolicy,
    deserialize_envelope,
    envelope_to_json_dict,
    envelope_to_json_str,
    parse_envelope_json,
    serialize_envelope,
)
from event_envelope.trace import TraceContextPolicy, normalize_traceparent, validate_traceparent

__all__ = [
    "DEFAULT_ENVELOPE_LIMITS",
    "ENVELOPE_COMPATIBILITY_POLICY",
    "AggregateScope",
    "DataClassification",
    "EnvelopeCompatibility",
    "EnvelopeLimits",
    "EnvelopeMetadata",
    "CONSUMER_SERDE_POLICY",
    "PRODUCER_SERDE_POLICY",
    "EnvelopeSerdePolicy",
    "EnvelopeValidationError",
    "EventEnvelope",
    "MediaRef",
    "MessageKind",
    "SUPPORTED_ENVELOPE_VERSION",
    "TraceContextPolicy",
    "UnknownFieldPolicy",
    "UnsupportedEnvelopeVersionError",
    "deserialize_envelope",
    "envelope_to_json_dict",
    "envelope_to_json_str",
    "normalize_traceparent",
    "parse_envelope_json",
    "serialize_envelope",
    "validate_traceparent",
]

__version__ = "0.1.0"
