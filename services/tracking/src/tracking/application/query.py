"""Timeline query application service."""

from __future__ import annotations

from uuid import UUID

from tracking.domain.types import ShipmentTimelineEntry, TimelinePage, TimelinePageCursor
from tracking.ports import TimelineQueryPort


class TimelineQueryService:
    """Application-layer read port for shipment timeline projections."""

    def __init__(self, *, query_port: TimelineQueryPort, max_page_size: int = 100) -> None:
        if max_page_size < 1:
            msg = "max_page_size must be at least 1"
            raise ValueError(msg)
        self._query_port = query_port
        self._max_page_size = max_page_size

    def get_by_event_id(self, event_id: UUID) -> ShipmentTimelineEntry | None:
        return self._query_port.get_by_event_id(event_id)

    def list_by_shipment_id(
        self,
        *,
        shipment_id: UUID,
        cursor: TimelinePageCursor | None = None,
        page_size: int = 50,
    ) -> TimelinePage:
        bounded = min(max(page_size, 1), self._max_page_size)
        return self._query_port.list_by_shipment_id(
            shipment_id=shipment_id,
            cursor=cursor,
            page_size=bounded,
        )
