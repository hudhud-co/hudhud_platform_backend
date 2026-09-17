"""Writing a Finance fact into the transactional outbox.

Kept in one place so every service posts a fact the same way, in the same transaction as
the state change that caused it. A fact written in a second transaction can outlive a
rolled-back change, which for money means announcing a settlement that did not happen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from finance.domain.messaging import OutboxRecord, OutboxStatus
from finance.ports.repository import FinanceUnitOfWork

DEFAULT_MAX_ATTEMPTS = 5


def enqueue(
    unit_of_work: FinanceUnitOfWork,
    payload_json: dict,
    subject: str,
    *,
    aggregate_id: UUID,
    aggregate_version: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> UUID:
    """Stage one fact. Must be called inside an open transaction."""
    moment = datetime.now(tz=UTC)
    event_id = UUID(payload_json["event_id"])
    unit_of_work.outbox.insert(
        OutboxRecord(
            id=uuid4(),
            event_id=event_id,
            subject=subject,
            event_type=payload_json["event_type"],
            event_version=int(payload_json["event_version"]),
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            payload_json=payload_json,
            status=OutboxStatus.PENDING,
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=moment,
            created_at=moment,
        )
    )
    return event_id
