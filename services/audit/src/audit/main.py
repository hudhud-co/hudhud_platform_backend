"""FastAPI composition root — health only; NATS consumer is not started here."""

from __future__ import annotations

from fastapi import FastAPI

from audit import __version__
from audit.api.health import router as health_router
from audit.config import AuditSettings, RuntimeEnvironment, load_settings


def create_app(settings: AuditSettings | None = None) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    app = FastAPI(
        title="HUDHUD Audit",
        version=__version__,
        description="Legacy A2 observation consumer (not canonical Audit facts)",
    )
    app.include_router(health_router)
    app.state.settings = resolved
    return app


app = create_app()
