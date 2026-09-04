"""FastAPI composition root — health, readiness, and acceptance command API."""

from __future__ import annotations

from fastapi import FastAPI

from shipment import __version__
from shipment.api.acceptance import router as acceptance_router
from shipment.api.health import router as health_router
from shipment.application.acceptance_service import AcceptanceLifecycleService
from shipment.application.readiness import evaluate_readiness
from shipment.config import RuntimeEnvironment, ShipmentSettings, load_settings
from shipment.infrastructure.authorizers.default_deny import DefaultDenyAcceptanceAuthorizer
from shipment.infrastructure.persistence.acceptance_uow import SqlAlchemyAcceptanceUnitOfWork
from shipment.infrastructure.persistence.session import (
    build_async_engine,
    build_async_session_factory,
    build_engine,
)
from shipment.ports.authorization import AcceptanceAuthorizer
from shipment.ports.repository import AcceptanceUnitOfWork


def create_app(
    settings: ShipmentSettings | None = None,
    *,
    unit_of_work: AcceptanceUnitOfWork | None = None,
    acceptance_authorizer: AcceptanceAuthorizer | None = None,
    nats_reachable: bool = False,
    nats_binding_verified: bool = False,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    uow = unit_of_work

    if uow is None and resolved.database_url:
        async_engine = build_async_engine(resolved.database_url)
        uow = SqlAlchemyAcceptanceUnitOfWork(build_async_session_factory(async_engine))
        engine = build_engine(_sync_database_url(resolved.database_url))
        persistence_wired = True
    elif uow is not None:
        persistence_wired = True
        if resolved.database_url:
            engine = build_engine(_sync_database_url(resolved.database_url))

    authorizer = acceptance_authorizer or DefaultDenyAcceptanceAuthorizer()
    acceptance_service = AcceptanceLifecycleService(uow) if uow is not None else None

    app = FastAPI(
        title="HUDHUD Shipment",
        version=__version__,
        description="Canonical shipment lifecycle writer — acceptance command API (W16-A)",
    )
    app.include_router(health_router)
    if acceptance_service is not None:
        app.include_router(acceptance_router)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.acceptance_service = acceptance_service
    app.state.acceptance_authorizer = authorizer
    app.state.unit_of_work = uow
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        authorization_adapter_ready=authorizer.is_production_ready,
        nats_reachable=nats_reachable,
        nats_binding_verified=nats_binding_verified,
    )
    return app


def _sync_database_url(database_url: str) -> str:
    """Map async driver URLs to a sync driver for readiness pings."""
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "+psycopg", 1)
    return database_url


app = create_app()
