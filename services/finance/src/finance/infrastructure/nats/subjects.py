"""Finance publish subject allowlist and JetStream stream binding."""

from __future__ import annotations

COD_SETTLED_SUBJECT = "hudhud.finance.finance.fact.cod_settled.v1"
PAYOUT_REQUESTED_SUBJECT = "hudhud.finance.finance.fact.payout_requested.v1"
PAYOUT_DECIDED_SUBJECT = "hudhud.finance.finance.fact.payout_decided.v1"
CASH_LIMIT_BREACHED_SUBJECT = "hudhud.finance.finance.fact.cash_limit_breached.v1"
STREAM_FINANCE = "HUDHUD_FINANCE"

ALLOWED_SUBJECTS: frozenset[str] = frozenset(
    {
        COD_SETTLED_SUBJECT,
        PAYOUT_REQUESTED_SUBJECT,
        PAYOUT_DECIDED_SUBJECT,
        CASH_LIMIT_BREACHED_SUBJECT,
    }
)

#: Subjects Finance consumes. Delivery states what happened at the door; Finance decides
#: what it means for the books (ADR-0012). Finance never publishes to these.
CONSUMED_SUBJECTS: frozenset[str] = frozenset(
    {
        "hudhud.delivery.delivery.fact.cod_collected.v1",
        "hudhud.delivery.delivery.fact.attempt_failed.v1",
    }
)

SUBJECT_TO_STREAM: dict[str, str] = dict.fromkeys(ALLOWED_SUBJECTS, STREAM_FINANCE)


def expected_stream_for_subject(subject: str) -> str | None:
    return SUBJECT_TO_STREAM.get(subject)


def validate_subject_allowed(subject: str) -> None:
    if subject not in ALLOWED_SUBJECTS:
        msg = f"subject not allowlisted: {subject}"
        raise ValueError(msg)
