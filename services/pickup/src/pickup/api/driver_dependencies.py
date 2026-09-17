"""Shared HTTP dependencies for driver, sender, hub, and operations commands."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from pickup.api.dependencies import require_bearer_token, require_idempotency_key
from pickup.application.acceptance_service import PickupAcceptanceService
from pickup.application.driver_session_service import DriverWorkSessionService
from pickup.application.handover_discovery_service import HandoverDiscoveryService
from pickup.application.handover_verification_service import (
    CourierHandoverVerificationService,
)
from pickup.application.hub_handover_service import HubHandoverService
from pickup.application.offline_sync_service import OfflineSyncService
from pickup.application.stop_service import PickupStopService
from pickup.application.task_lifecycle_service import PickupTaskLifecycleService
from pickup.composition import build_driver_services
from pickup.ports.authorization import (
    AuthorizationOutcome,
    PickupActor,
    PickupAuthorizer,
    PickupCommand,
    PickupRole,
)
from pickup.ports.recovery_authorizer import AuthorizerUnavailableError


def _driver_services(request: Request):
    """Build this request's driver services, over this request's unit of work.

    They used to be built once at startup and shared. Each holds a reference to a unit
    of work, which holds one session — so every concurrent caller was using the same
    one. Building them here costs a few object allocations and makes the request the
    unit of isolation, which is what it always should have been.
    """
    factory = getattr(request.app.state, "unit_of_work_factory", None)
    settings = getattr(request.app.state, "settings", None)
    if factory is None or settings is None:
        raise HTTPException(status_code=503, detail="pickup driver features unavailable")
    services = build_driver_services(settings, factory())
    if services is None:
        raise HTTPException(status_code=503, detail="pickup driver features unavailable")
    return services


def _require(service, label: str):
    if service is None:
        raise HTTPException(status_code=503, detail=f"{label} unavailable")
    return service


def get_acceptance_service(request: Request) -> PickupAcceptanceService:
    return _require(
        _driver_services(request).acceptance_service, "pickup acceptance service"
    )


def get_work_session_service(request: Request) -> DriverWorkSessionService:
    return _require(
        _driver_services(request).work_session_service, "driver work session service"
    )


def get_task_lifecycle_service(request: Request) -> PickupTaskLifecycleService:
    return _require(
        _driver_services(request).task_lifecycle_service, "pickup task lifecycle service"
    )


def get_stop_service(request: Request) -> PickupStopService:
    return _require(_driver_services(request).stop_service, "pickup stop service")


def get_verification_service(request: Request) -> CourierHandoverVerificationService:
    return _require(
        _driver_services(request).verification_service,
        "courier handover verification service",
    )


def get_handover_discovery_service(request: Request) -> HandoverDiscoveryService:
    return _require(
        _driver_services(request).handover_discovery_service,
        "handover discovery service",
    )


def get_hub_handover_service(request: Request) -> HubHandoverService:
    return _require(
        _driver_services(request).hub_handover_service, "hub handover service"
    )


def get_offline_sync_service(request: Request) -> OfflineSyncService:
    return _require(_driver_services(request).offline_sync_service, "offline sync service")


def get_pickup_authorizer(request: Request) -> PickupAuthorizer:
    authorizer = getattr(request.app.state, "pickup_authorizer", None)
    if authorizer is None:
        raise HTTPException(status_code=503, detail="authorization unavailable")
    return authorizer


async def authorize(
    *,
    authorizer: PickupAuthorizer,
    bearer_token: str,
    command: PickupCommand,
    resource_id: UUID | None = None,
    require_role: PickupRole | None = None,
) -> PickupActor:
    """Resolve the actor cryptographically or refuse. Never trusts request content."""
    try:
        decision = await authorizer.authorize(
            bearer_token=bearer_token,
            command=command,
            resource_id=resource_id,
        )
    except AuthorizerUnavailableError:
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if decision.outcome is AuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail="authentication required")
    if decision.outcome is AuthorizationOutcome.FORBIDDEN or decision.actor is None:
        raise HTTPException(status_code=403, detail="forbidden")
    actor = decision.actor
    if require_role is not None and not actor.has_role(require_role):
        raise HTTPException(status_code=403, detail="forbidden")
    return actor


BearerToken = Annotated[str, Depends(require_bearer_token)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
Authorizer = Annotated[PickupAuthorizer, Depends(get_pickup_authorizer)]
