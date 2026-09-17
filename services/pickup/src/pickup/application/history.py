"""Append-only Pickup audit history helper.

Actor identity is always taken from the authorization decision. Callers must never
pass an identity from a request body or a forwarded header.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pickup.domain.entities import TaskHistoryEntry
from pickup.domain.sanitize import sanitize_error_message
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import TaskHistoryRepository

MAX_DETAIL_VALUE_LENGTH = 256


def record_history(
    repository: TaskHistoryRepository,
    *,
    pickup_task_id: UUID,
    action: str,
    actor: PickupActor,
    previous_status: str | None,
    new_status: str | None,
    occurred_at: datetime,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> TaskHistoryEntry:
    entry = TaskHistoryEntry(
        history_id=uuid4(),
        pickup_task_id=pickup_task_id,
        action=action,
        actor_id=actor.actor_id,
        actor_role=actor.primary_role,
        previous_status=previous_status,
        new_status=new_status,
        occurred_at=occurred_at,
        request_id=request_id,
        details=_sanitize_details(details or {}),
    )
    repository.append_entry(entry)
    return entry


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    """Audit metadata is operator-visible: never let a secret or blob through."""
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        if isinstance(value, bool | int | float):
            clean[key] = value
            continue
        clean[key] = sanitize_error_message(str(value), max_length=MAX_DETAIL_VALUE_LENGTH)
    return clean
