"""Merchant publish subject allowlist and JetStream stream binding.

The relay may publish only subjects Merchant owns and has registered a contract for, so a
malformed or foreign outbox row can never become a published event.
"""

from __future__ import annotations

APPLICATION_DECIDED_SUBJECT = "hudhud.merchant.merchant.fact.application_decided.v1"
TEAM_MEMBERSHIP_CHANGED_SUBJECT = (
    "hudhud.merchant.merchant.fact.team_membership_changed.v1"
)
STREAM_MERCHANT = "HUDHUD_MERCHANT"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {APPLICATION_DECIDED_SUBJECT, TEAM_MEMBERSHIP_CHANGED_SUBJECT}
)

SUBJECT_TO_STREAM: dict[str, str] = {
    APPLICATION_DECIDED_SUBJECT: STREAM_MERCHANT,
    TEAM_MEMBERSHIP_CHANGED_SUBJECT: STREAM_MERCHANT,
}


def expected_stream_for_subject(subject: str) -> str | None:
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
