"""Acceptance command HTTP adapter tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RegisterPickupTaskCommand,
)
from shipment.config import RuntimeEnvironment, load_settings
from shipment.domain.value_objects import AcceptanceOutcome
from shipment.infrastructure.authorizers.default_deny import DefaultDenyAcceptanceAuthorizer
from shipment.infrastructure.authorizers.fake import FakeAcceptanceAuthorizer
from shipment.infrastructure.memory import InMemoryAcceptanceUnitOfWork
from shipment.main import create_app
from shipment.ports.authorization import AcceptanceAccessDecision, AuthenticatedActor

SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"


async def _seed(store: InMemoryAcceptanceUnitOfWork, *, driver_id: str = "driver-42"):
    service = AcceptanceLifecycleService(store)
    _order, shipment = await service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-HTTP-1",
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    pickup_task_id = uuid4()
    await service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=True,
        )
    )
    return shipment, pickup_task_id


def _seed_sync(store: InMemoryAcceptanceUnitOfWork, *, driver_id: str = "driver-42"):
    return asyncio.run(_seed(store, driver_id=driver_id))


def _client(
    store: InMemoryAcceptanceUnitOfWork,
    *,
    authorizer: FakeAcceptanceAuthorizer | DefaultDenyAcceptanceAuthorizer | None = None,
) -> TestClient:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    app = create_app(
        settings,
        unit_of_work=store,
        acceptance_authorizer=authorizer or FakeAcceptanceAuthorizer(),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_liveness_only() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    client = _client(store, authorizer=DefaultDenyAcceptanceAuthorizer())
    assert client.get("/health").json() == {"status": "ok", "service": "shipment"}


def test_ready_requires_authorization_adapter_in_staging() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        database_url="postgresql+asyncpg://localhost/shipment",
    )
    app = create_app(
        settings,
        unit_of_work=store,
        acceptance_authorizer=DefaultDenyAcceptanceAuthorizer(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert "authorization_adapter_not_ready" in ready.json()["blockers"]
    assert client.get("/health").json()["status"] == "ok"


def test_ready_passes_in_test_with_fake_authorizer() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    client = _client(store)
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_acceptance_scan_success_and_idempotent_replay() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(store)
    payload = {
        "pickup_task_id": str(pickup_task_id),
        "scanned_identifier": "WB-HTTP-1",
        "scan_timestamp": "2026-05-31T10:00:00+00:00",
        "outcome": AcceptanceOutcome.ACCEPTED.value,
    }
    headers = {
        "Authorization": f"Bearer {SENSITIVE_TOKEN}",
        "Idempotency-Key": "http-idem-1",
    }
    first = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["current_status"] == "IN_CUSTODY"
    assert body["idempotent_replay"] is False
    assert SENSITIVE_TOKEN not in first.text

    second = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json=payload,
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True


def test_missing_idempotency_key_rejected() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(store)
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={"Authorization": f"Bearer {SENSITIVE_TOKEN}"},
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_identity_headers_and_body_actor_fields_are_ignored_or_rejected() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store, driver_id="driver-from-auth")
    authorizer = FakeAcceptanceAuthorizer(actor_user_id="driver-from-auth")
    client = _client(store, authorizer=authorizer)

    rejected = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
            "acting_driver_user_id": "attacker",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-2a",
        },
    )
    assert rejected.status_code == 422

    accepted = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-2b",
            "X-User-Id": "attacker",
            "X-Role": "admin",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["current_custody_id"] == "driver-from-auth"
    assert authorizer.seen_tokens == [SENSITIVE_TOKEN]


def test_default_deny_authorizer_returns_401() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    app = create_app(
        settings,
        unit_of_work=store,
        acceptance_authorizer=DefaultDenyAcceptanceAuthorizer(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-3",
        },
    )
    assert response.status_code == 401


def test_forbidden_authorizer_returns_403() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(
        store,
        authorizer=FakeAcceptanceAuthorizer(
            decision=AcceptanceAccessDecision.forbidden(),
        ),
    )
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-4",
        },
    )
    assert response.status_code == 403


def test_authorizer_unavailable_returns_503() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(store, authorizer=FakeAcceptanceAuthorizer(unavailable=True))
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-5",
        },
    )
    assert response.status_code == 503


def test_domain_not_found_maps_to_404() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    client = _client(store)
    response = client.post(
        f"/v1/shipments/{uuid4()}/acceptance-scans",
        json={
            "pickup_task_id": str(uuid4()),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-6",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "not found"


def test_wrong_actor_maps_to_422() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store, driver_id="assigned-driver")
    client = _client(
        store,
        authorizer=FakeAcceptanceAuthorizer(
            decision=AcceptanceAccessDecision.allow(AuthenticatedActor(user_id="other-driver")),
        ),
    )
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-idem-7",
        },
    )
    assert response.status_code == 422
    assert "prerequisites" in response.json()["detail"]


def test_uow_source_has_no_sync_over_async_helpers() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "shipment"
        / "infrastructure"
        / "persistence"
        / "acceptance_uow.py"
    ).read_text(encoding="utf-8")
    assert "run_until_complete" not in source
    assert "asyncio.run" not in source
    assert "_run_async" not in source
    assert "async def begin" in source
    assert "async def commit" in source
