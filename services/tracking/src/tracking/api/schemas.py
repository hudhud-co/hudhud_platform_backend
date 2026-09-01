"""Safe HTTP response models — no Bridge/CDC/internal persistence fields."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TimelineEntryResponse(BaseModel):
    """Presentation-safe timeline entry for HTTP clients."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    occurred_at: datetime
    legacy_event_type: str
    previous_status: str | None = None
    new_status: str | None = None


class TimelinePageResponse(BaseModel):
    """Paginated shipment timeline — display ordering is occurred_at + event_id."""

    model_config = ConfigDict(frozen=True)

    shipment_id: UUID
    entries: tuple[TimelineEntryResponse, ...] = Field(default_factory=tuple)
    next_cursor: str | None = None
