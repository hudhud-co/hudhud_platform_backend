"""HTTP adapter for merchant-stop outcomes, scan resolution and acceptance status."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from driver_fixtures import DRIVER_ID, SIGNING_KEY, register_task
from fastapi.testclient import TestClient

from pickup.api.driver_schemas import RefusePickupRequest
from pickup.config import RuntimeEnvironment, load_settings
from pickup.domain.value_objects import AssignmentState, PickupTaskStatus
from pickup.infrastructure.authorizers.pickup_fake import FakePickupAuthorizer
from pickup.infrastructure.fake_shipment_eligibility import (
    InMemoryShipmentEligibilityAdapter,
)
from pickup.infrastructure.memory import InMemoryPickupUnitOfWork
from pickup.main import create_app
from pickup.ports.authorization import PickupActor, PickupCommand, PickupRole

DRIVER_TOKEN = "driver-token"  # noqa: S105 - test fixture, not a credential
OTHER_DRIVER_TOKEN = "other-driver-token"  # noqa: S105 - test fixture
MERCHANT_TOKEN = "merchant-token"  # noqa: S105 - test fixture

TOKEN_ACTORS = {
    DRIVER_TOKEN: PickupActor(
        actor_id=DRIVER_ID, roles=frozenset({PickupRole.PICKUP_DRIVER})
    ),
    OTHER_DRIVER_TOKEN: PickupActor(
        actor_id="driver-99", roles=frozenset({PickupRole.PICKUP_DRIVER})
    ),
    MERCHANT_TOKEN: PickupActor(
        actor_id="merchant-user-7", roles=frozenset({PickupRole.MERCHANT_MEMBER})
    ),
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


def _acknowledged(store, *, batch_id=None, driver_user_id=DRIVER_ID):
    return register_task(
        store,
        driver_user_id=driver_user_id,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch_id,
    )


# ----------------------------------------------------------------- refusal


def test_refusing_a_parcel_returns_the_stop_outcome(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/refuse",
        json={"reason": "TOO_WEAK_PACKAGING", "notes": "box collapsing"},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stop_outcome"] == "REFUSED"
    assert body["stop_outcome_reason"] == "TOO_WEAK_PACKAGING"
    assert body["acceptance_state"] is None


def test_refusal_requires_a_known_reason(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/refuse",
        json={"reason": "JUST_BECAUSE"},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "exception_evidence_insufficient"


def test_a_second_conflicting_outcome_is_a_conflict(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)
    client.post(
        f"/pickup/tasks/{task.pickup_task_id}/refuse",
        json={"reason": "DAMAGED_BEFORE_PICKUP"},
        headers=_auth(DRIVER_TOKEN),
    )

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/not-presented",
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pickup_stop_outcome_already_recorded"


def test_a_merchant_may_not_refuse_a_parcel(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/refuse",
        json={"reason": "TOO_WEAK_PACKAGING"},
        headers=_auth(MERCHANT_TOKEN),
    )

    assert response.status_code == 403


def test_refusal_without_a_token_is_unauthenticated(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/refuse",
        json={"reason": "TOO_WEAK_PACKAGING"},
    )

    assert response.status_code == 401


def test_the_refusal_request_body_never_carries_actor_identity() -> None:
    assert set(RefusePickupRequest.model_fields) == {"reason", "notes"}


# ---------------------------------------------------------- not presented


def test_marking_not_presented_returns_the_stop_outcome(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/not-presented",
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stop_outcome"] == "NOT_PRESENTED"
    assert body["stop_outcome_reason"] is None


def test_marking_not_presented_twice_replays(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)
    url = f"/pickup/tasks/{task.pickup_task_id}/not-presented"
    client.post(url, headers=_auth(DRIVER_TOKEN))

    response = client.post(url, headers=_auth(DRIVER_TOKEN))

    assert response.status_code == 200
    assert response.json()["idempotent_replay"] is True


# ------------------------------------------------------ condition decision


def _progress_to_scanned(client: TestClient, task) -> None:
    client.post(
        f"/pickup/tasks/{task.pickup_task_id}/arrive", headers=_auth(DRIVER_TOKEN)
    )
    client.post(
        f"/pickup/tasks/{task.pickup_task_id}/scan",
        json={"scanned_identifier": "HHD-10452"},
        headers=_auth(DRIVER_TOKEN),
    )


def test_a_packaging_decision_is_returned_on_the_task(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    _progress_to_scanned(client, task)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/condition-proof",
        json={
            "package_condition_status": "GOOD",
            "packaging_assessment": "BORDERLINE",
            "decision": "ACCEPT_WITH_WARNING",
            "condition_notes": "weak seam",
            "evidence_present": True,
        },
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["packaging_assessment"] == "BORDERLINE"
    assert body["condition_decision"] == "ACCEPT_WITH_WARNING"


def test_accepting_packaging_that_is_too_weak_is_refused_with_409(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    _progress_to_scanned(client, task)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/condition-proof",
        json={
            "package_condition_status": "MAJOR_DAMAGE",
            "packaging_assessment": "TOO_WEAK",
            "decision": "ACCEPT_WITH_WARNING",
            "condition_notes": "will not survive",
            "evidence_present": True,
        },
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["code"] == "pickup_packaging_decision_not_permitted"
    )


def test_an_existing_client_may_omit_the_new_condition_fields(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)
    client.post("/pickup/work-sessions/start", json={}, headers=_auth(DRIVER_TOKEN))
    _progress_to_scanned(client, task)

    response = client.post(
        f"/pickup/tasks/{task.pickup_task_id}/condition-proof",
        json={"package_condition_status": "GOOD"},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PROOF_CAPTURED"


# --------------------------------------------------------- scan resolution


def test_resolving_a_label_expected_here_reports_valid(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    task = _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": str(task.shipment_id)},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "VALID"
    assert body["can_proceed"] is True
    assert body["pickup_task_id"] == str(task.pickup_task_id)


def test_resolving_an_unregistered_label_reports_unknown(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": "NOPE-1"},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "UNKNOWN_LABEL"
    assert response.json()["can_proceed"] is False


def test_an_unreadable_label_resolves_without_naming_a_task(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    task = _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": str(task.shipment_id), "unreadable": True},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "UNREADABLE"
    assert response.json()["pickup_task_id"] is None


def test_a_blank_scan_is_rejected(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": ""},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "scan_identifier_missing"


def test_a_cancelled_shipment_resolves_as_cancelled(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    task = register_task(
        store,
        status=PickupTaskStatus.CANCELLED,
        assignment_state=AssignmentState.ACKNOWLEDGED,
        assigned_batch_id=batch,
    )

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": str(task.shipment_id)},
        headers=_auth(DRIVER_TOKEN),
    )

    assert response.json()["outcome"] == "CANCELLED_SHIPMENT"


def test_scan_resolution_is_scoped_to_the_authenticated_driver(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    task = _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": str(task.shipment_id)},
        headers=_auth(OTHER_DRIVER_TOKEN),
    )

    assert response.json()["outcome"] == "UNKNOWN_LABEL"


def test_a_merchant_may_not_resolve_a_scan(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    _acknowledged(store, batch_id=batch)

    response = client.post(
        f"/pickup/batches/{batch}/scan-resolution",
        json={"scanned_identifier": "x"},
        headers=_auth(MERCHANT_TOKEN),
    )

    assert response.status_code == 403


# ---------------------------------------------------------- stop readiness


def test_stop_readiness_blocks_completion_while_parcels_are_unresolved(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    _acknowledged(store, batch_id=batch)
    _acknowledged(store, batch_id=batch)

    response = client.get(f"/pickup/batches/{batch}/stop", headers=_auth(DRIVER_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["expected"] == 2
    assert body["unresolved"] == 2
    assert body["can_complete"] is False
    assert "every expected parcel needs an outcome" in body["blocking_reason"]


def test_stop_readiness_allows_completion_once_everything_is_resolved(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    batch = uuid4()
    first = _acknowledged(store, batch_id=batch)
    second = _acknowledged(store, batch_id=batch)
    client.post(
        f"/pickup/tasks/{first.pickup_task_id}/refuse",
        json={"reason": "DOES_NOT_MATCH_SHIPMENT"},
        headers=_auth(DRIVER_TOKEN),
    )
    client.post(
        f"/pickup/tasks/{second.pickup_task_id}/not-presented",
        headers=_auth(DRIVER_TOKEN),
    )

    response = client.get(f"/pickup/batches/{batch}/stop", headers=_auth(DRIVER_TOKEN))

    body = response.json()
    assert body["can_complete"] is True
    assert body["is_partial"] is True
    assert body["blocking_reason"] is None


def test_an_unknown_batch_is_a_404(client: TestClient) -> None:
    response = client.get(f"/pickup/batches/{uuid4()}/stop", headers=_auth(DRIVER_TOKEN))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "pickup_batch_not_found"


# ------------------------------------------------------- acceptance status


def test_acceptance_status_reports_safe_to_retry_when_nothing_was_recorded(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.get(
        f"/pickup/tasks/{task.pickup_task_id}/acceptance", headers=_auth(DRIVER_TOKEN)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recorded"] is False
    assert body["safe_to_retry"] is True


def test_acceptance_status_is_not_readable_by_another_driver(
    client: TestClient, store: InMemoryPickupUnitOfWork
) -> None:
    task = _acknowledged(store)

    response = client.get(
        f"/pickup/tasks/{task.pickup_task_id}/acceptance",
        headers=_auth(OTHER_DRIVER_TOKEN),
    )

    assert response.status_code == 404


def test_acceptance_status_of_an_unknown_task_is_a_404(client: TestClient) -> None:
    response = client.get(
        f"/pickup/tasks/{uuid4()}/acceptance", headers=_auth(DRIVER_TOKEN)
    )

    assert response.status_code == 404


def test_reading_the_acceptance_status_is_denied_without_the_right_command(
    store: InMemoryPickupUnitOfWork,
) -> None:
    denying = FakePickupAuthorizer(
        token_actors=TOKEN_ACTORS,
        denied_commands=frozenset({PickupCommand.TASK_READ_ACCEPTANCE}),
        production_ready=True,
    )
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        shipment_eligibility=InMemoryShipmentEligibilityAdapter(production_ready=True),
        pickup_authorizer=denying,
    )
    task = _acknowledged(store)
    with TestClient(app) as test_client:
        response = test_client.get(
            f"/pickup/tasks/{task.pickup_task_id}/acceptance",
            headers=_auth(DRIVER_TOKEN),
        )

    assert response.status_code == 403


# ------------------------------------------------------------- surface shape


def test_every_new_route_is_registered_on_the_driver_surface(
    client: TestClient,
) -> None:
    paths = set(client.app.openapi()["paths"])

    assert "/pickup/tasks/{pickup_task_id}/refuse" in paths
    assert "/pickup/tasks/{pickup_task_id}/not-presented" in paths
    assert "/pickup/tasks/{pickup_task_id}/acceptance" in paths
    assert "/pickup/batches/{assigned_batch_id}/scan-resolution" in paths
    assert "/pickup/batches/{assigned_batch_id}/stop" in paths
