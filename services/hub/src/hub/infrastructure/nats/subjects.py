"""Hub publish subject allowlist and JetStream stream binding."""

from __future__ import annotations

DROP_OFF_ACCEPTED_SUBJECT = "hudhud.hub.hub.fact.drop_off_accepted.v1"
PARCEL_HELD_SUBJECT = "hudhud.hub.hub.fact.parcel_held.v1"
STREAM_HUB = "HUDHUD_HUB"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {DROP_OFF_ACCEPTED_SUBJECT, PARCEL_HELD_SUBJECT}
)

SUBJECT_TO_STREAM: dict[str, str] = {
    DROP_OFF_ACCEPTED_SUBJECT: STREAM_HUB,
    PARCEL_HELD_SUBJECT: STREAM_HUB,
}


def expected_stream_for_subject(subject: str) -> str | None:
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
