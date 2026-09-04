"""Health and readiness HTTP adapter tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from pickup.config import RuntimeEnvironment, load_settings
from pickup.infrastructure.authorizers.fake import FakeRecoveryAuthorizer
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.memory import InMemoryRecoveryUnitOfWork
from pickup.main import create_app


def test_health_is_liveness_only() -> None:
    app = create_app(load_settings(environment=RuntimeEnvironment.TEST))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "pickup"}


def test_ready_blocks_default_production_adapters() -> None:
    app = create_app(load_settings(environment=RuntimeEnvironment.LOCAL))
    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "not_ready"
    assert "authorization_adapter_not_configured" in body["blockers"]
    assert "shipment_eligibility_adapter_deferred" in body["blockers"]
    assert client.get("/health").json()["status"] == "ok"


def test_ready_passes_with_injected_test_adapters() -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    app = create_app(
        settings,
        unit_of_work=InMemoryRecoveryUnitOfWork(),
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        recovery_authorizer=FakeRecoveryAuthorizer(production_ready=True),
    )
    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["checks"]["authorization_configured"] is True
    assert body["checks"]["shipment_eligibility_configured"] is True
