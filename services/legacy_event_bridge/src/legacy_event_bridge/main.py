"""FastAPI composition root."""

from __future__ import annotations

from fastapi import FastAPI

from legacy_event_bridge import __version__
from legacy_event_bridge.api.health import router as health_router
from legacy_event_bridge.application.readiness import evaluate_readiness
from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment, load_settings
from legacy_event_bridge.infrastructure.nats.client import build_live_nats_client
from legacy_event_bridge.infrastructure.nats.publisher import JetStreamPublisherAdapter
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
    nats_reachable: bool | None = None
    nats_adapter: JetStreamPublisherAdapter | None = None

    if resolved.database_url:
        engine = build_engine(resolved.database_url)
        _ = SqlAlchemyBridgeStore(session_factory=build_session_factory(engine))
        persistence_wired = True

    if resolved.relay_enabled:
        try:
            nats_client = build_live_nats_client(resolved)
            nats_client.connect()
            nats_adapter = JetStreamPublisherAdapter(
                nats_client,
                publish_timeout_seconds=resolved.relay_publish_timeout_seconds,
                transport_max_msg_bytes=resolved.relay_transport_max_msg_bytes,
            )
            nats_reachable = nats_adapter.ping()
        except Exception:
            nats_reachable = False

    app = FastAPI(
        title="Legacy Event Bridge",
        version=__version__,
        description="Transitional CDC observation pipeline (ADR-0007)",
    )
    app.include_router(health_router)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.nats_adapter = nats_adapter
    app.state.readiness_report = evaluate_readiness(
        settings=resolved,
        engine=engine,
        persistence_wired=persistence_wired,
        nats_reachable=nats_reachable,
    )
    return app


app = create_app()
