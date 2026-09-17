"""HTTP adapter: authorization enforcement, identity source, and error mapping."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from driver_fixtures import DRIVER_ID, SIGNING_KEY, register_task
from fastapi.testclient import TestClient

from pickup.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from pickup.infrastructure.authorizers.pickup_fake import FakePickupAuthorizer
from pickup.infrastructure.fake_shipment_eligibility import (
    InMemoryShipmentEligibilityAdapter,
)
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.main import create_app
from pickup.ports.authorization import PickupActor, PickupCommand, PickupRole

DRIVER_TOKEN = "driver-token"  # noqa: S105 - test fixture, not a credential
MERCHANT_TOKEN = "merchant-token"  # noqa: S105 - test fixture, not a credential
HUB_TOKEN = "hub-token"  # noqa: S105 - test fixture, not a credential
OPS_TOKEN = "ops-token"  # noqa: S105 - test fixture, not a credential
HUB_ID = uuid4()

TOKEN_ACTORS = {
    DRIVER_TOKEN: PickupActor(
        actor_id=DRIVER_ID, roles=frozenset({PickupRole.PICKUP_DRIVER})
    ),
    MERCHANT_TOKEN: PickupActor(
        actor_id="merchant-user-7", roles=frozenset({PickupRole.MERCHANT_MEMBER})
    ),
    HUB_TOKEN: PickupActor(
        actor_id="hub-operator-7",
        roles=frozenset({PickupRole.HUB_OPERATOR}),
        hub_ids=frozenset({HUB_ID}),
    ),
    OPS_TOKEN: PickupActor(actor_id="ops-3", roles=frozenset({PickupRole.OPERATIONS})),
}


@pytest.fixture
def store() -> InMemoryPickupUnitOfWork:
    return InMemoryPickupUnitOfWork()


@pytest.fixture
def authorizer() -> FakePickupAuthorizer:
    return FakePickupAuthorizer(token_actors=TOKEN_ACTORS, production_ready=True)


@pytest.fixture
def client(
    store: InMemoryPickupUnitOfWork, authorizer: FakePickupAuthorizer
) -> Iterator[TestClient]:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=authorizer,
    )
    with TestClient(app) as test_client:
        yield test_client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------ authentication


def test_a_missing_bearer_token_is_unauthenticated(client: TestClient) -> None:
    response = client.post("/pickup/work-sessions/start", json={})
    assert response.status_code == 401


def test_an_unknown_token_is_unauthenticated(client: TestClient) -> None:
    response = client.post(
        "/pickup/work-sessions/start", json={}, headers=_auth("who-is-this")
    )
    assert response.status_code == 401


def test_the_wrong_role_is_forbidden(client: TestClient) -> None:
    response = client.post(
        "/pickup/work-sessions/start", json={}, headers=_auth(MERCHANT_TOKEN)
    )
    assert response.status_code == 403


def test_identity_comes_from_the_token_not_the_request_body(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    response = client.post(
        "/pickup/work-sessions/start",
        json={"driver_user_id": "driver-impersonated"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 200
    assert response.json()["driver_user_id"] == DRIVER_ID


def test_the_default_deny_authorizer_refuses_everything() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=InMemoryPickupUnitOfWork(),
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN)
        )
    assert response.status_code == 401


def test_an_unavailable_authorizer_maps_to_service_unavailable(
    store: InMemoryPickupUnitOfWork,
) -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=FakePickupAuthorizer(unavailable=True),
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN)
        )
    assert response.status_code == 503


def test_a_denied_command_is_forbidden(store: InMemoryPickupUnitOfWork) -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=FakePickupAuthorizer(
            token_actors=TOKEN_ACTORS,
            denied_commands=frozenset({PickupCommand.WORK_SESSION_START}),
        ),
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN)
        )
    assert response.status_code == 403


# ------------------------------------------------------------------ sessions


def test_the_full_work_session_round_trip_over_http(client: TestClient) -> None:
    started = client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    assert started.json()["availability"] == "ONLINE"

    paused = client.post(
        f"/pickup/work-sessions/{session_id}/pause",
        json={"reason": "BREAK"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert paused.json()["availability"] == "ON_BREAK"

    resumed = client.post(
        f"/pickup/work-sessions/{session_id}/resume", headers=_auth(DRIVER_TOKEN)
    )
    assert resumed.json()["availability"] == "ONLINE"

    ended = client.post(
        f"/pickup/work-sessions/{session_id}/end",
        json={"reason": "SESSION_COMPLETE"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert ended.json()["availability"] == "OFFLINE"
    assert ended.json()["workload"]["end_blockers"] == []


def test_current_session_read_reports_offline_when_none_is_open(
    client: TestClient,
) -> None:
    response = client.get("/pickup/work-sessions/current", headers=_auth(DRIVER_TOKEN))
    assert response.status_code == 200
    assert response.json()["availability"] == "OFFLINE"


def test_an_invalid_reason_maps_to_422(client: TestClient) -> None:
    started = client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    session_id = started.json()["session_id"]
    response = client.post(
        f"/pickup/work-sessions/{session_id}/pause",
        json={"reason": "TELEPORTED"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_work_session_reason"


# ---------------------------------------------------------------------- tasks


def test_task_progress_over_http_and_audit_history(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    task = register_task(store)
    task_id = task.pickup_task_id

    assert (
        client.post(
            f"/pickup/tasks/{task_id}/acknowledge", headers=_auth(DRIVER_TOKEN)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/pickup/tasks/{task_id}/arrive", headers=_auth(DRIVER_TOKEN)
        ).status_code
        == 200
    )
    scanned = client.post(
        f"/pickup/tasks/{task_id}/scan",
        json={"scanned_identifier": "WB-1001"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert scanned.json()["status"] == "SCANNED"

    history = client.get(f"/pickup/tasks/{task_id}/history", headers=_auth(DRIVER_TOKEN))
    actions = [entry["action"] for entry in history.json()["entries"]]
    assert actions == ["assignment_acknowledged", "task_arrived", "task_scanned"]
    assert {entry["actor_id"] for entry in history.json()["entries"]} == {DRIVER_ID}


def test_progress_without_acknowledgement_maps_to_409(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    task = register_task(store)
    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/arrive", headers=_auth(DRIVER_TOKEN)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "assignment_not_acknowledged"


def test_acknowledging_without_an_online_session_maps_to_409(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = register_task(store)
    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/acknowledge", headers=_auth(DRIVER_TOKEN)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "driver_not_available_for_assignment"


def test_an_unknown_task_maps_to_404(client: TestClient) -> None:
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    response = client.post(
        f"/pickup/tasks/{uuid4()}/acknowledge", headers=_auth(DRIVER_TOKEN)
    )
    assert response.status_code == 404


# ------------------------------------------------------------------ ceremony


def _progress_to_proof(client: TestClient, store: InMemoryPickupUnitOfWork):
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    task = register_task(store)
    task_id = task.pickup_task_id
    client.post(f"/pickup/tasks/{task_id}/acknowledge", headers=_auth(DRIVER_TOKEN))
    client.post(f"/pickup/tasks/{task_id}/arrive", headers=_auth(DRIVER_TOKEN))
    client.post(
        f"/pickup/tasks/{task_id}/scan",
        json={"scanned_identifier": "WB-1001"},
        headers=_auth(DRIVER_TOKEN),
    )
    client.post(
        f"/pickup/tasks/{task_id}/condition-proof",
        json={"package_condition_status": "GOOD"},
        headers=_auth(DRIVER_TOKEN),
    )
    return task


def test_the_sender_ceremony_round_trip_over_http(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _progress_to_proof(client, store)
    task_id = task.pickup_task_id

    issued = client.post(
        f"/pickup/tasks/{task_id}/courier-challenge", headers=_auth(DRIVER_TOKEN)
    )
    assert issued.status_code == 201
    payload = issued.json()["challenge_payload"]

    verified = client.post(
        f"/pickup/tasks/{task_id}/courier-challenge/verify",
        json={"presented_payload": payload},
        headers=_auth(MERCHANT_TOKEN),
    )
    assert verified.status_code == 200
    assert verified.json()["courier_user_id"] == DRIVER_ID

    submitted = client.post(
        f"/pickup/tasks/{task_id}/courier-manifest",
        json={"scanned_identifier": "WB-1001"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert submitted.status_code == 201

    confirmed = client.post(
        f"/pickup/tasks/{task_id}/courier-manifest/confirm",
        json={},
        headers=_auth(MERCHANT_TOKEN),
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    accepted = client.post(
        f"/pickup/tasks/{task_id}/accept",
        json={"scanned_identifier": "WB-1001", "outcome": "ACCEPTED"},
        headers={**_auth(DRIVER_TOKEN), "Idempotency-Key": "accept-http-1"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["acceptance_state"] == "ACCEPTED"
    assert store.outbox.list_for_aggregate(task_id)[0].event_type == "pickup.fact.accepted"


def test_acceptance_without_the_ceremony_maps_to_409(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _progress_to_proof(client, store)
    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/accept",
        json={"scanned_identifier": "WB-1001"},
        headers={**_auth(DRIVER_TOKEN), "Idempotency-Key": "accept-http-2"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "courier_verification_required"


def test_acceptance_requires_an_idempotency_key(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _progress_to_proof(client, store)
    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/accept",
        json={"scanned_identifier": "WB-1001"},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 400


def test_a_driver_cannot_verify_its_own_challenge_over_http(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _progress_to_proof(client, store)
    issued = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/courier-challenge",
        headers=_auth(DRIVER_TOKEN),
    )
    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/courier-challenge/verify",
        json={"presented_payload": issued.json()["challenge_payload"]},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "sender_may_not_verify_own_courier"


# ------------------------------------------------------------- hub handover


def test_the_hub_handover_round_trip_over_http(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _progress_to_proof(client, store)
    task_id = task.pickup_task_id
    issued = client.post(
        f"/pickup/tasks/{task_id}/courier-challenge", headers=_auth(DRIVER_TOKEN)
    )
    client.post(
        f"/pickup/tasks/{task_id}/courier-challenge/verify",
        json={"presented_payload": issued.json()["challenge_payload"]},
        headers=_auth(MERCHANT_TOKEN),
    )
    client.post(
        f"/pickup/tasks/{task_id}/courier-manifest",
        json={"scanned_identifier": "WB-1001"},
        headers=_auth(DRIVER_TOKEN),
    )
    client.post(
        f"/pickup/tasks/{task_id}/courier-manifest/confirm",
        json={},
        headers=_auth(MERCHANT_TOKEN),
    )
    client.post(
        f"/pickup/tasks/{task_id}/accept",
        json={"scanned_identifier": "WB-1001"},
        headers={**_auth(DRIVER_TOKEN), "Idempotency-Key": "accept-http-3"},
    )

    created = client.post(
        "/pickup/handover-manifests",
        json={"hub_id": str(HUB_ID), "pickup_task_ids": [str(task_id)]},
        headers=_auth(DRIVER_TOKEN),
    )
    assert created.status_code == 201
    manifest_id = created.json()["manifest_id"]

    client.post(
        f"/pickup/handover-manifests/{manifest_id}/arrive", headers=_auth(DRIVER_TOKEN)
    )
    receipt = client.post(
        f"/pickup/handover-manifests/{manifest_id}/receipts",
        json={"shipment_id": str(task.shipment_id), "receiving_hub_id": str(HUB_ID)},
        headers=_auth(HUB_TOKEN),
    )
    assert receipt.status_code == 200
    assert receipt.json()["custody_released"] is True
    assert receipt.json()["manifest"]["status"] == "COMPLETED"

    facts = [
        row.event_type for row in store.outbox.list_for_aggregate(task_id)
    ]
    assert facts == ["pickup.fact.accepted", "pickup.fact.handover_completed"]


def test_a_driver_cannot_record_a_hub_receipt(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = register_task(store)
    response = client.post(
        f"/pickup/handover-manifests/{uuid4()}/receipts",
        json={"shipment_id": str(task.shipment_id), "receiving_hub_id": str(HUB_ID)},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 403


def test_a_hub_operator_outside_its_scope_is_refused(client: TestClient) -> None:
    response = client.post(
        f"/pickup/handover-manifests/{uuid4()}/receipts",
        json={"shipment_id": str(uuid4()), "receiving_hub_id": str(uuid4())},
        headers=_auth(HUB_TOKEN),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "hub_scope_not_authorized"


# ---------------------------------------------------------------- offline


def test_offline_authorization_is_issued_once_with_its_token(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    task = register_task(store)
    client.post(
        f"/pickup/tasks/{task.pickup_task_id}/acknowledge", headers=_auth(DRIVER_TOKEN)
    )
    response = client.post(
        "/pickup/offline/authorizations",
        json={"device_id": "device-abcdef123456", "pickup_task_id": str(task.pickup_task_id)},
        headers=_auth(DRIVER_TOKEN),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["authorization_token"]
    assert body["client_requirements"]["no_undownloaded_work"] is True
    assert "ACCEPT_CUSTODY" not in body["authorization"]["permitted_operations"]


def test_reconciliation_cases_are_operations_only(client: TestClient) -> None:
    driver_view = client.get(
        "/pickup/offline/reconciliation-cases", headers=_auth(DRIVER_TOKEN)
    )
    assert driver_view.status_code == 403
    ops_view = client.get(
        "/pickup/offline/reconciliation-cases", headers=_auth(OPS_TOKEN)
    )
    assert ops_view.status_code == 200
    assert ops_view.json()["cases"] == []


def test_offline_routes_are_absent_without_a_signing_key(
    store: InMemoryPickupUnitOfWork, authorizer: FakePickupAuthorizer
) -> None:
    """Handover and offline fail closed rather than running unsigned."""
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=None),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=authorizer,
    )
    paths = app.openapi()["paths"]
    assert "/pickup/offline/sync" not in paths
    assert "/pickup/tasks/{pickup_task_id}/courier-challenge" not in paths
    assert "/pickup/work-sessions/start" in paths


def test_acceptance_is_closed_when_the_ceremony_gate_cannot_be_composed(
    store: InMemoryPickupUnitOfWork, authorizer: FakePickupAuthorizer
) -> None:
    """No signing key must close custody start, not silently ungate it."""
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=None),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=authorizer,
    )
    # Asserted through the API rather than through `app.state`: the services are built
    # per request now, so there is nothing on the app to inspect — and what matters was
    # never the attribute, it was that custody cannot start.
    with TestClient(app) as test_client:
        response = test_client.post(
            f"/pickup/tasks/{uuid4()}/accept",
            json={"scanned_identifier": "WB-1001"},
            headers={**_auth(DRIVER_TOKEN), "Idempotency-Key": "accept-closed"},
        )
    assert response.status_code == 503


def test_disabling_verification_is_refused_in_production() -> None:
    with pytest.raises(ProductionStartupBlockedError, match="require_courier_verification"):
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql://placeholder/db",
            signing_key=SIGNING_KEY,
            require_courier_verification=False,
        ).assert_production_gates()


def test_production_startup_requires_a_signing_key() -> None:
    with pytest.raises(ProductionStartupBlockedError, match="PICKUP_SIGNING_KEY"):
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql://placeholder/db",
            signing_key=None,
        ).assert_production_gates()


# --------------------------------------------------------- handover discovery


def test_handover_discovery_is_readable_by_the_sender(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = register_task(store)
    response = client.get(
        f"/pickup/shipments/{task.shipment_id}/handover", headers=_auth(MERCHANT_TOKEN)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["shipment_id"] == str(task.shipment_id)
    assert body["pickup_task_id"] == str(task.pickup_task_id)
    assert body["state"] == "AWAITING_COURIER_VERIFICATION"
    assert body["actionable"] is True
    # Nothing that could stand in for the ceremony itself leaks through the read.
    assert "assigned_driver_user_id" not in body
    assert "payload" not in body


def test_handover_discovery_for_an_unknown_shipment_is_not_found(
    client: TestClient,
) -> None:
    response = client.get(
        f"/pickup/shipments/{uuid4()}/handover", headers=_auth(MERCHANT_TOKEN)
    )
    assert response.status_code == 404


def test_handover_discovery_requires_authentication(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = register_task(store)
    response = client.get(f"/pickup/shipments/{task.shipment_id}/handover")
    assert response.status_code == 401


def test_a_denied_handover_discovery_command_is_forbidden(
    store: InMemoryPickupUnitOfWork,
) -> None:
    task = register_task(store)
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=FakePickupAuthorizer(
            token_actors=TOKEN_ACTORS,
            denied_commands=frozenset({PickupCommand.HANDOVER_DISCOVERY_READ}),
        ),
    )
    with TestClient(app) as test_client:
        response = test_client.get(
            f"/pickup/shipments/{task.shipment_id}/handover",
            headers=_auth(MERCHANT_TOKEN),
        )
    assert response.status_code == 403
