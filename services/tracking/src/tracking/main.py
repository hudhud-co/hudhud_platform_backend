"""FastAPI composition root — health only; NATS consumer is not started here."""

from __future__ import annotations

from fastapi import FastAPI

from tracking import __version__
from tracking.api.health import router as health_router
from tracking.application.readiness import evaluate_readiness
from tracking.config import RuntimeEnvironment, TrackingSettings, load_settings
from tracking.infrastructure.persistence.session import build_engine, build_session_factory
from tracking.infrastructure.persistence.sqlalchemy_store import SqlAlchemyTrackingStore


def create_app(
    settings: TrackingSettings | None = None,
    *,
    nats_reachable: bool = False,
    nats_binding_verified: bool = False,
) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    if resolved.database_url:
        engine = build_engine(resolved.database_url)
        _ = SqlAlchemyTrackingStore(session_factory=build_session_factory(engine))
        persistence_wired = True

    app = FastAPI(
        title="HUDHUD Tracking",
        version=__version__,
        description="Legacy A1 timeline observation consumer (not Shipment authority)",
    )
    app.include_router(health_router)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        nats_reachable=nats_reachable,
        nats_binding_verified=nats_binding_verified,
    )
    return app


app = create_app()
