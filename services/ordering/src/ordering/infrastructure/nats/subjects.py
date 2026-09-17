"""Ordering publish subject allowlist and JetStream stream binding.

The relay may publish only subjects Ordering owns and has registered a contract for, so a
malformed or foreign outbox row can never become a published event.
"""

from __future__ import annotations

SHIPMENT_REGISTERED_SUBJECT = "hudhud.order.order.fact.shipment_registered.v1"
SHIPMENT_CANCELLED_SUBJECT = "hudhud.order.order.fact.shipment_cancelled.v1"
STREAM_ORDER = "HUDHUD_ORDER"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {SHIPMENT_REGISTERED_SUBJECT, SHIPMENT_CANCELLED_SUBJECT}
)

SUBJECT_TO_STREAM: dict[str, str] = {
    SHIPMENT_REGISTERED_SUBJECT: STREAM_ORDER,
    SHIPMENT_CANCELLED_SUBJECT: STREAM_ORDER,
}


def expected_stream_for_subject(subject: str) -> str | None:
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
