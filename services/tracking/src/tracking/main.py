"""FastAPI composition root — health, readiness, and authenticated timeline query."""

from __future__ import annotations

from fastapi import FastAPI

from tracking import __version__
from tracking.api.health import router as health_router
from tracking.api.timeline import router as timeline_router
from tracking.application.query import TimelineQueryService
from tracking.application.readiness import evaluate_readiness
from tracking.config import RuntimeEnvironment, TrackingSettings, load_settings
from tracking.infrastructure.authorizers.factory import (
    _authorizer_readiness_flags,
    build_query_authorizer,
)
from tracking.infrastructure.jwt.jwks_client import JwksClient
from tracking.infrastructure.persistence.session import build_engine, build_session_factory
from tracking.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyTimelineQuery,
    SqlAlchemyTrackingStore,
)
from tracking.ports import TimelineQueryPort
from tracking.ports.query_authorizer import TrackingQueryAuthorizer
from tracking.ports.shipment_access import ShipmentAccessPolicy

DEFAULT_MAX_PAGE_SIZE = 100


def create_app(
    settings: TrackingSettings | None = None,
    *,
    query_authorizer: TrackingQueryAuthorizer | None = None,
    query_port: TimelineQueryPort | None = None,
    jwks_client: JwksClient | None = None,
    shipment_policy: ShipmentAccessPolicy | None = None,
    jwks_available: bool | None = None,
    nats_reachable: bool = False,
    nats_binding_verified: bool = False,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()
    resolved.assert_query_auth_gates()

    engine = None
    persistence_wired = False
    timeline_query_port: TimelineQueryPort | None = query_port

    if timeline_query_port is None and resolved.database_url:
        engine = build_engine(resolved.database_url)
        session_factory = build_session_factory(engine)
        _ = SqlAlchemyTrackingStore(session_factory=session_factory)
        timeline_query_port = SqlAlchemyTimelineQuery(session_factory=session_factory)
        persistence_wired = True

    authorizer = query_authorizer or build_query_authorizer(
        resolved,
        jwks_client=jwks_client,
        shipment_policy=shipment_policy,
        jwks_available=jwks_available,
    )

    timeline_query_service: TimelineQueryService | None = None
    if timeline_query_port is not None:
        timeline_query_service = TimelineQueryService(
            query_port=timeline_query_port,
            max_page_size=DEFAULT_MAX_PAGE_SIZE,
        )

    app = FastAPI(
        title="HUDHUD Tracking",
        version=__version__,
        description="Legacy A1 timeline observation consumer (not Shipment authority)",
    )
    app.include_router(health_router)
    if timeline_query_service is not None:
        app.include_router(timeline_router)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.timeline_query_service = timeline_query_service
    app.state.query_authorizer = authorizer
    (
        query_authorizer_configured,
        jwt_verifier_configured,
        jwks_dependency_available,
        shipment_access_policy_configured,
    ) = _authorizer_readiness_flags(authorizer)
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        query_authorizer_configured=query_authorizer_configured,
        jwt_verifier_configured=jwt_verifier_configured,
        jwks_dependency_available=jwks_dependency_available,
        shipment_access_policy_configured=shipment_access_policy_configured,
        query_persistence_configured=timeline_query_service is not None,
        nats_reachable=nats_reachable,
        nats_binding_verified=nats_binding_verified,
    )
    return app


app = create_app()
