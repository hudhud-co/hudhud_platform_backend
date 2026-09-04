"""HTTP adapter dependencies — bearer extraction only; no identity headers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from shipment.application.acceptance_service import AcceptanceLifecycleService
from shipment.ports.authorization import AcceptanceAuthorizer


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    return parts[1]


def get_acceptance_service(request: Request) -> AcceptanceLifecycleService:
    service = getattr(request.app.state, "acceptance_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="acceptance service unavailable")
    return service


def get_acceptance_authorizer(request: Request) -> AcceptanceAuthorizer:
    authorizer = getattr(request.app.state, "acceptance_authorizer", None)
    if authorizer is None:
        raise HTTPException(status_code=503, detail="authorization unavailable")
    return authorizer


def require_bearer_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    token = extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return token


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    return idempotency_key.strip()
