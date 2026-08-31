"""Health endpoint smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from legacy_event_bridge.config import RuntimeEnvironment, load_settings
from legacy_event_bridge.main import create_app


def test_health_and_ready() -> None:
    app = create_app(load_settings(environment=RuntimeEnvironment.TEST))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert ready["checks"]["cdc_adapter_deferred"]
    assert "live_cdc_adapter" in ready["blockers"]
