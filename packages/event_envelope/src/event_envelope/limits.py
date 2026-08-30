"""Configurable envelope size limits (provisional — not architectural constants)."""

from __future__ import annotations

from dataclasses import dataclass

from event_envelope.errors import EnvelopeValidationError


@dataclass(frozen=True, slots=True)
class EnvelopeLimits:
    """Production size limits remain configurable and provisional per ADR-0002."""

    soft_payload_bytes: int = 64 * 1024
    hard_envelope_bytes: int = 256 * 1024

    def validate_envelope_size(self, serialized_bytes: int) -> None:
        if serialized_bytes > self.hard_envelope_bytes:
            raise EnvelopeValidationError(
                "envelope_size",
                f"serialized envelope exceeds hard limit ({self.hard_envelope_bytes} bytes)",
            )


DEFAULT_ENVELOPE_LIMITS = EnvelopeLimits()
