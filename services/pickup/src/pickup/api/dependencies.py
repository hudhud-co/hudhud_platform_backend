"""HTTP adapter dependencies — bearer and idempotency only; no identity headers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Request

from pickup.application.recovery_service import PickupRecoveryService
from pickup.ports.recovery_authorizer import RecoveryAuthorizer


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    return parts[1]


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
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")
    key = idempotency_key.strip()
    if len(key) > 256:
        raise HTTPException(status_code=400, detail="Idempotency-Key too long")
    return key


def get_recovery_service(request: Request) -> PickupRecoveryService:
    service = getattr(request.app.state, "recovery_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="recovery service unavailable")
    return service


def get_recovery_authorizer(request: Request) -> RecoveryAuthorizer:
    authorizer = getattr(request.app.state, "recovery_authorizer", None)
    if authorizer is None:
        raise HTTPException(status_code=503, detail="authorization unavailable")
    return authorizer
