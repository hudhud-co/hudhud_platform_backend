"""Envelope schema compatibility (distinct from payload-schema compatibility)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from event_envelope.errors import UnsupportedEnvelopeVersionError

SUPPORTED_ENVELOPE_VERSION = 1


class UnknownFieldPolicy(StrEnum):
    """How unknown top-level envelope fields are handled on deserialize."""

    IGNORE = "ignore"
    PRESERVE = "preserve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class EnvelopeCompatibility:
    """Envelope-level compatibility rules (not ``event_version`` / payload schema)."""

    supported_envelope_version: int = SUPPORTED_ENVELOPE_VERSION
    min_envelope_version: int = 1
    unknown_field_policy: UnknownFieldPolicy = UnknownFieldPolicy.PRESERVE

    def validate_envelope_version(self, envelope_version: int) -> None:
        if envelope_version < self.min_envelope_version:
            raise UnsupportedEnvelopeVersionError(
                envelope_version,
                self.supported_envelope_version,
            )
        if envelope_version > self.supported_envelope_version:
            raise UnsupportedEnvelopeVersionError(
                envelope_version,
                self.supported_envelope_version,
            )

    def is_additive_change(self, *, added_fields: frozenset[str]) -> bool:
        """New optional top-level fields are additive within the same envelope version."""
        return bool(added_fields)

    def requires_envelope_version_bump(self, *, removed_fields: frozenset[str]) -> bool:
        """Removing or renaming envelope fields requires a new envelope version."""
        return bool(removed_fields)


ENVELOPE_COMPATIBILITY_POLICY = EnvelopeCompatibility()

# Documented upgrade expectations (machine-readable summary for contract consumers).
ENVELOPE_UPGRADE_EXPECTATIONS: dict[str, str] = {
    "producer_ahead": (
        "Producers MUST NOT emit envelope_version greater than the consumer's supported version."
    ),
    "consumer_behind": (
        "Consumers on envelope_version N MUST preserve or explicitly reject unknown additive "
        "fields — silent lossy dropping is forbidden as the default interoperability behavior."
    ),
    "producer_strict": (
        "Producers and build-time validation MUST reject unknown top-level envelope fields."
    ),
    "breaking_change": (
        "Removing or renaming required envelope fields requires incrementing envelope_version."
    ),
    "payload_independent": (
        "event_version governs payload-schema compatibility; envelope_version governs wire shape."
    ),
}
