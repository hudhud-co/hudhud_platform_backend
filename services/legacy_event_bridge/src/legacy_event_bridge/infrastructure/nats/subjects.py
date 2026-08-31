"""A1/A2 subject allowlist and JetStream stream binding."""

from __future__ import annotations

A1_SUBJECT = "hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1"
A2_SUBJECT = "hudhud.audit.legacy_bridge.observation.audit_entry.v1"

STREAM_SHIPMENT = "HUDHUD_SHIPMENT"
STREAM_AUDIT = "HUDHUD_AUDIT"

ALLOWED_SUBJECTS: frozenset[str] = frozenset({A1_SUBJECT, A2_SUBJECT})

SUBJECT_TO_STREAM: dict[str, str] = {
    A1_SUBJECT: STREAM_SHIPMENT,
    A2_SUBJECT: STREAM_AUDIT,
}


def expected_stream_for_subject(subject: str) -> str | None:
    """Return the topology stream name for an allowlisted subject."""
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    """Raise ValueError when subject is outside the Bridge allowlist."""
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
