"""HTTP adapter dependencies — bearer extraction only; no identity headers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from tracking.application.query import TimelineQueryService
from tracking.ports.query_authorizer import TrackingQueryAuthorizer


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    return parts[1]


def get_timeline_query_service(request: Request) -> TimelineQueryService:
    service = getattr(request.app.state, "timeline_query_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="timeline query unavailable")
    return service


def get_query_authorizer(request: Request) -> TrackingQueryAuthorizer:
    authorizer = getattr(request.app.state, "query_authorizer", None)
    if authorizer is None:
        raise HTTPException(status_code=503, detail="query authorizer unavailable")
    return authorizer


def require_bearer_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    token = extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return token
