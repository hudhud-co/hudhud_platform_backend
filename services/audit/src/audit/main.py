"""FastAPI composition root — health only; NATS consumer is not started here."""

from __future__ import annotations

from fastapi import FastAPI

from audit import __version__
from audit.api.health import router as health_router
from audit.application.readiness import evaluate_readiness
from audit.config import AuditSettings, RuntimeEnvironment, load_settings
from audit.infrastructure.persistence.session import build_engine, build_session_factory
from audit.infrastructure.persistence.sqlalchemy_store import SqlAlchemyAuditStore


def create_app(settings: AuditSettings | None = None) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    engine = None
    persistence_wired = False
    if resolved.database_url:
        engine = build_engine(resolved.database_url)
        _ = SqlAlchemyAuditStore(session_factory=build_session_factory(engine))
        persistence_wired = True

    app = FastAPI(
        title="HUDHUD Audit",
        version=__version__,
        description="Legacy A2 observation consumer (not canonical Audit facts)",
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
