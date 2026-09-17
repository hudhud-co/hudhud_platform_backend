"""Delivery publish subject allowlist and JetStream stream binding."""

from __future__ import annotations

DELIVERED_SUBJECT = "hudhud.delivery.delivery.fact.delivered.v1"
ATTEMPT_FAILED_SUBJECT = "hudhud.delivery.delivery.fact.attempt_failed.v1"
COD_COLLECTED_SUBJECT = "hudhud.delivery.delivery.fact.cod_collected.v1"
DEPARTED_SUBJECT = "hudhud.delivery.delivery.fact.departed_for_receiver.v1"
STREAM_DELIVERY = "HUDHUD_DELIVERY"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {
        DELIVERED_SUBJECT,
        ATTEMPT_FAILED_SUBJECT,
        COD_COLLECTED_SUBJECT,
        DEPARTED_SUBJECT,
    }
)

SUBJECT_TO_STREAM: dict[str, str] = dict.fromkeys(ALLOWED_SUBJECTS, STREAM_DELIVERY)


def expected_stream_for_subject(subject: str) -> str | None:
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
