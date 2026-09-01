"""Health endpoint smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tracking.config import RuntimeEnvironment, load_settings
from tracking.main import create_app


def test_health_and_ready() -> None:
    app = create_app(load_settings(environment=RuntimeEnvironment.TEST))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    assert client.get("/health").json()["service"] == "tracking"


def test_health_liveness_when_not_ready() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    app = create_app(settings)
    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert "nats_unreachable" in ready.json()["blockers"]
    assert client.get("/health").json()["status"] == "ok"


def test_ready_reports_nats_binding_when_verified() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    app = create_app(
        settings,
        nats_reachable=True,
        nats_binding_verified=True,
    )
    client = TestClient(app)
    assert client.get("/ready").json()["status"] == "ready"
