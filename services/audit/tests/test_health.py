"""Health endpoint smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from audit.config import RuntimeEnvironment, load_settings
from audit.main import create_app


def test_health_and_ready() -> None:
    app = create_app(load_settings(environment=RuntimeEnvironment.TEST))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    assert client.get("/health").json()["service"] == "audit"
