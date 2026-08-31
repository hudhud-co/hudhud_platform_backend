"""FastAPI composition root."""

from __future__ import annotations

from fastapi import FastAPI

from legacy_event_bridge import __version__
from legacy_event_bridge.api.health import router as health_router
from legacy_event_bridge.application.readiness import evaluate_readiness
from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment, load_settings
from legacy_event_bridge.infrastructure.persistence.postgres_store import SqlAlchemyBridgeStore
from legacy_event_bridge.infrastructure.persistence.session import (
    build_engine,
    build_session_factory,
)


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    if resolved.database_url:
        engine = build_engine(resolved.database_url)
        _ = SqlAlchemyBridgeStore(session_factory=build_session_factory(engine))
        persistence_wired = True

    app = FastAPI(
        title="Legacy Event Bridge",
        version=__version__,
        description="Transitional CDC observation pipeline (ADR-0007)",
    )
    app.include_router(health_router)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
    )
    return app


app = create_app()
