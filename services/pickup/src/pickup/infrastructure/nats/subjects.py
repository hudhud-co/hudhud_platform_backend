"""Pickup publish subject allowlist and JetStream stream binding.

The relay may publish only subjects Pickup owns and has registered a contract for.
Anything else is rejected before it reaches the broker, so a malformed or foreign
outbox row can never become a published event.
"""

from __future__ import annotations

ACCEPTED_SUBJECT = "hudhud.pickup.pickup.fact.accepted.v1"
HANDOVER_COMPLETED_SUBJECT = "hudhud.pickup.pickup.fact.handover_completed.v1"
STREAM_PICKUP = "HUDHUD_PICKUP"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {ACCEPTED_SUBJECT, HANDOVER_COMPLETED_SUBJECT}
)

SUBJECT_TO_STREAM: dict[str, str] = {
    ACCEPTED_SUBJECT: STREAM_PICKUP,
    HANDOVER_COMPLETED_SUBJECT: STREAM_PICKUP,
}


def expected_stream_for_subject(subject: str) -> str | None:
    """Return the topology stream name for an allowlisted subject."""
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    """Raise ValueError when subject is outside the Pickup publish allowlist."""
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
