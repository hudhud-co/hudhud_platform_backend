"""FastAPI composition root."""

from __future__ import annotations

from fastapi import FastAPI

from legacy_event_bridge import __version__
from legacy_event_bridge.api.health import router as health_router
from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment, load_settings


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    resolved = settings or load_settings(environment=RuntimeEnvironment.LOCAL)
    resolved.assert_production_gates()

    app = FastAPI(
        title="Legacy Event Bridge",
        version=__version__,
        description="Transitional CDC observation pipeline (ADR-0007)",
    )
    app.include_router(health_router)
    app.state.settings = resolved
    return app


app = create_app()
