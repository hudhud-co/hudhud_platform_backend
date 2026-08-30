"""Configurable envelope size limits (provisional — not architectural constants)."""

from __future__ import annotations

from dataclasses import dataclass

from event_envelope.errors import EnvelopeValidationError

# Provisional transport ceiling — align with infra/eventing `STREAM_MAX_MSG_SIZE` / JetStream
# `max_msg_size_bytes`. Values are configurable and not frozen architectural constants.
PROVISIONAL_HARD_ENVELOPE_BYTES = 256 * 1024
PROVISIONAL_NATS_MAX_MSG_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class EnvelopeLimits:
    """Production size limits remain configurable and provisional per ADR-0002."""

    soft_payload_bytes: int = 64 * 1024
    hard_envelope_bytes: int = PROVISIONAL_HARD_ENVELOPE_BYTES
    transport_max_msg_bytes: int = PROVISIONAL_NATS_MAX_MSG_BYTES

    def validate_envelope_size(self, serialized_bytes: int) -> None:
        if serialized_bytes > self.hard_envelope_bytes:
            raise EnvelopeValidationError(
                "envelope_size",
                f"serialized envelope exceeds hard limit ({self.hard_envelope_bytes} bytes)",
            )

    def validate_transport_coherence(self) -> None:
        if self.transport_max_msg_bytes < self.hard_envelope_bytes:
            raise EnvelopeValidationError(
                "transport_max_msg_bytes",
                "transport max message size must be >= envelope hard limit",
            )


DEFAULT_ENVELOPE_LIMITS = EnvelopeLimits()
