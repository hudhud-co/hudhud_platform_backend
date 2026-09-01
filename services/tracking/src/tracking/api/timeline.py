"""Authenticated timeline query HTTP adapter."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from tracking.api.dependencies import (
    get_query_authorizer,
    get_timeline_query_service,
    require_bearer_token,
)
from tracking.api.schemas import TimelineEntryResponse, TimelinePageResponse
from tracking.api.timeline_cursor import (
    TimelineCursorError,
    decode_timeline_cursor,
    encode_timeline_cursor,
)
from tracking.application.query import TimelineQueryService
from tracking.domain.types import ShipmentTimelineEntry
from tracking.ports.query_authorizer import (
    AuthorizerUnavailableError,
    TrackingAuthorizationOutcome,
    TrackingQueryAuthorizer,
)

router = APIRouter(prefix="/tracking", tags=["tracking"])


def _to_entry_response(entry: ShipmentTimelineEntry) -> TimelineEntryResponse:
    return TimelineEntryResponse(
        event_id=entry.event_id,
        occurred_at=entry.occurred_at,
        legacy_event_type=entry.legacy_event_type,
        previous_status=entry.old_status,
        new_status=entry.new_status,
    )


@router.get("/shipments/{shipment_id}/timeline", response_model=TimelinePageResponse)
async def get_shipment_timeline(
    shipment_id: UUID,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    query_service: Annotated[TimelineQueryService, Depends(get_timeline_query_service)],
    authorizer: Annotated[TrackingQueryAuthorizer, Depends(get_query_authorizer)],
    limit: Annotated[int, Query(ge=1)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> TimelinePageResponse:
    """Return shipment timeline entries ordered by occurred_at then event_id (display order)."""
    try:
        decision = await authorizer.authorize_timeline_read(
            bearer_token=bearer_token,
            shipment_id=shipment_id,
        )
    except AuthorizerUnavailableError:
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail="authentication required")
    if decision.outcome is TrackingAuthorizationOutcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail="forbidden")

    page_cursor = None
    if cursor is not None:
        try:
            page_cursor = decode_timeline_cursor(shipment_id, cursor)
        except TimelineCursorError:
            raise HTTPException(status_code=422, detail="invalid cursor") from None

    try:
        page = query_service.list_by_shipment_id(
            shipment_id=shipment_id,
            cursor=page_cursor,
            page_size=limit,
        )
    except Exception:
        raise HTTPException(status_code=503, detail="timeline query unavailable") from None

    next_cursor = None
    if page.next_cursor is not None:
        next_cursor = encode_timeline_cursor(shipment_id, page.next_cursor)

    return TimelinePageResponse(
        shipment_id=shipment_id,
        entries=tuple(_to_entry_response(entry) for entry in page.entries),
        next_cursor=next_cursor,
    )
