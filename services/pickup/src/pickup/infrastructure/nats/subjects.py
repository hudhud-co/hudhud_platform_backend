"""pickup.fact.accepted subject allowlist and JetStream stream binding."""

from __future__ import annotations

ACCEPTED_SUBJECT = "hudhud.pickup.pickup.fact.accepted.v1"
STREAM_PICKUP = "HUDHUD_PICKUP"

ALLOWED_SUBJECTS: frozenset[str] = frozenset({ACCEPTED_SUBJECT})

SUBJECT_TO_STREAM: dict[str, str] = {
    ACCEPTED_SUBJECT: STREAM_PICKUP,
}


def expected_stream_for_subject(subject: str) -> str | None:
    """Return the topology stream name for an allowlisted subject."""
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    """Raise ValueError when subject is outside the Pickup accepted-fact allowlist."""
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
