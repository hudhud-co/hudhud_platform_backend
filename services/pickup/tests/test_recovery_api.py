"""Pickup recovery command HTTP API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from pickup.application.recovery_service import PickupRecoveryService, RegisterPickupTaskCommand
from pickup.config import RuntimeEnvironment, load_settings
from pickup.domain.sanitize import sanitize_error_message
from pickup.domain.value_objects import (
    CustodyType,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ScheduledWindow,
    ShipmentStatus,
)
from pickup.infrastructure.authorizers.fake import FakeRecoveryAuthorizer
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.memory import InMemoryRecoveryUnitOfWork
from pickup.main import create_app
from pickup.ports.recovery_authorizer import RecoveryAccessDecision
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot

SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"

type _ClientBundle = tuple[
    TestClient,
    InMemoryRecoveryUnitOfWork,
    InMemoryShipmentEligibilityAdapter,
    FakeRecoveryAuthorizer,
]


def _now() -> datetime:
    return datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _window(*, hours_offset: int = 0) -> ScheduledWindow:
    start = _now() + timedelta(hours=hours_offset)
    return ScheduledWindow(start=start, end=start + timedelta(hours=2))


def _build_client(*, authorizer: FakeRecoveryAuthorizer | None = None) -> _ClientBundle:
    store = InMemoryRecoveryUnitOfWork()
    eligibility = InMemoryShipmentEligibilityAdapter(production_ready=True)
    resolved_authorizer = authorizer or FakeRecoveryAuthorizer(production_ready=True)
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    app = create_app(
        settings,
        unit_of_work=store,
        shipment_eligibility=eligibility,
        recovery_authorizer=resolved_authorizer,
    )
    return TestClient(app), store, eligibility, resolved_authorizer


def _auth_headers(idempotency_key: str, token: str = SENSITIVE_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


def _seed_task(
    store: InMemoryRecoveryUnitOfWork,
    eligibility: InMemoryShipmentEligibilityAdapter,
    *,
    driver_id: str = "driver-42",
    window: ScheduledWindow | None = None,
) -> UUID:
    service = PickupRecoveryService(store, eligibility)
    shipment_id = uuid4()
    task_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.CREATED,
            custody_started=False,
            custody_type=None,
            custody_id=None,
        )
    )
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=uuid4(),
            scheduled_window=window or _window(),
            created_at=_now(),
        )
    )
    return task_id


def test_retry_requires_idempotency_key_and_bearer() -> None:
    client, store, eligibility, _authorizer = _build_client()
    task_id = _seed_task(store, eligibility)

    missing_key = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers={"Authorization": f"Bearer {SENSITIVE_TOKEN}"},
        json={"reason": "retry"},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["detail"] == "Idempotency-Key header required"

    missing_auth = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers={"Idempotency-Key": "retry-no-auth"},
        json={"reason": "retry"},
    )
    assert missing_auth.status_code == 401


def test_identity_headers_and_body_actor_are_ignored() -> None:
    authorizer = FakeRecoveryAuthorizer(production_ready=True, actor_id="from-token")
    client, store, eligibility, _ = _build_client(authorizer=authorizer)
    task_id = _seed_task(store, eligibility)

    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers={
            **_auth_headers("retry-ignore-headers"),
            "X-User-Id": "spoofed-user",
            "X-Actor-Id": "spoofed-actor",
            "X-Role": "admin",
        },
        json={"reason": "retry", "actor_id": "body-spoof", "user_id": "body-user"},
    )
    assert response.status_code == 200
    assert authorizer.seen_tokens == [SENSITIVE_TOKEN]
    assert "spoofed-user" not in authorizer.seen_tokens


def test_retry_reschedule_reassign_cancel_happy_paths() -> None:
    client, store, eligibility, _ = _build_client()
    task_id = _seed_task(store, eligibility)

    retry = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("retry-1"),
        json={"reason": "driver unavailable"},
    )
    assert retry.status_code == 200
    retry_body = retry.json()
    assert retry_body["action"] == "RETRY"
    assert retry_body["idempotent_replay"] is False
    assert retry_body["original_task"]["status"] == "SUPERSEDED"
    assert retry_body["replacement_task"]["attempt_number"] == 2
    replacement_id = UUID(retry_body["replacement_task"]["pickup_task_id"])

    new_window_start = (_now() + timedelta(hours=24)).isoformat()
    new_window_end = (_now() + timedelta(hours=26)).isoformat()
    reschedule = client.post(
        f"/pickup/tasks/{replacement_id}/reschedule",
        headers=_auth_headers("reschedule-1"),
        json={
            "reason": "later slot",
            "scheduled_window": {"start": new_window_start, "end": new_window_end},
        },
    )
    assert reschedule.status_code == 200
    assert reschedule.json()["action"] == "RESCHEDULE"
    rescheduled_id = UUID(reschedule.json()["replacement_task"]["pickup_task_id"])

    reassign = client.post(
        f"/pickup/tasks/{rescheduled_id}/reassign",
        headers=_auth_headers("reassign-1"),
        json={"reason": "coverage", "new_driver_user_id": "driver-new"},
    )
    assert reassign.status_code == 200
    assert reassign.json()["replacement_task"]["assigned_driver_user_id"] == "driver-new"
    reassigned_id = UUID(reassign.json()["replacement_task"]["pickup_task_id"])

    cancel = client.post(
        f"/pickup/tasks/{reassigned_id}/cancel",
        headers=_auth_headers("cancel-1"),
        json={"reason": "merchant cancelled"},
    )
    assert cancel.status_code == 200
    cancel_body = cancel.json()
    assert cancel_body["action"] == "CANCEL"
    assert cancel_body["replacement_task"] is None
    assert cancel_body["original_task"]["status"] == "CANCELLED"
    stored = store.pickup_tasks.get_pickup_task(reassigned_id)
    assert stored is not None
    assert stored.status.value == "CANCELLED"


def test_idempotent_replay_and_conflict() -> None:
    client, store, eligibility, _ = _build_client()
    task_id = _seed_task(store, eligibility)

    first = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("shared-key"),
        json={"reason": "retry"},
    )
    second = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("shared-key"),
        json={"reason": "retry"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert (
        second.json()["replacement_task"]["pickup_task_id"]
        == first.json()["replacement_task"]["pickup_task_id"]
    )

    conflict = client.post(
        f"/pickup/tasks/{task_id}/cancel",
        headers=_auth_headers("shared-key"),
        json={"reason": "cancel"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "conflicting_idempotency_key"


def test_domain_rejection_mapping() -> None:
    client, store, eligibility, _ = _build_client()
    task_id = _seed_task(store, eligibility)
    accepted = store.pickup_tasks.get_pickup_task(task_id)
    assert accepted is not None
    accepted.acceptance_state = PickupTaskAcceptanceState.ACCEPTED
    store.pickup_tasks.save_pickup_task(accepted)

    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("retry-accepted"),
        json={"reason": "should fail"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pickup_task_already_accepted"

    missing = client.post(
        f"/pickup/tasks/{uuid4()}/retry",
        headers=_auth_headers("retry-missing"),
        json={"reason": "missing"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "pickup_task_not_found"


def test_authorizer_outcomes() -> None:
    unauth = FakeRecoveryAuthorizer(
        decision=RecoveryAccessDecision.unauthenticated(),
        production_ready=True,
    )
    client, store, eligibility, _ = _build_client(authorizer=unauth)
    task_id = _seed_task(store, eligibility)
    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("unauth"),
        json={"reason": "retry"},
    )
    assert response.status_code == 401

    forbidden = FakeRecoveryAuthorizer(
        decision=RecoveryAccessDecision.forbidden(),
        production_ready=True,
    )
    client, store, eligibility, _ = _build_client(authorizer=forbidden)
    task_id = _seed_task(store, eligibility)
    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("forbidden"),
        json={"reason": "retry"},
    )
    assert response.status_code == 403

    unavailable = FakeRecoveryAuthorizer(unavailable=True, production_ready=True)
    client, store, eligibility, _ = _build_client(authorizer=unavailable)
    task_id = _seed_task(store, eligibility)
    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("unavailable"),
        json={"reason": "retry"},
    )
    assert response.status_code == 503
    assert SENSITIVE_TOKEN not in response.text


def test_error_responses_do_not_leak_secrets() -> None:
    leaked = sanitize_error_message(
        f"authorization=Bearer {SENSITIVE_TOKEN} password=hunter2 "
        "postgresql://user:secret@localhost/pickup"
    )
    assert SENSITIVE_TOKEN not in leaked
    assert "hunter2" not in leaked
    assert "secret@" not in leaked
    assert "[redacted]" in leaked


def test_invalid_reschedule_maps_to_422() -> None:
    client, store, eligibility, _ = _build_client()
    task_id = _seed_task(store, eligibility)
    start = _now().isoformat()
    response = client.post(
        f"/pickup/tasks/{task_id}/reschedule",
        headers=_auth_headers("bad-window"),
        json={
            "reason": "bad",
            "scheduled_window": {"start": start, "end": start},
        },
    )
    assert response.status_code == 422
    detail: Any = response.json()["detail"]
    assert detail["code"] == "invalid_reschedule_input"


def _seed_task_with_snapshot(
    store: InMemoryRecoveryUnitOfWork,
    eligibility: InMemoryShipmentEligibilityAdapter,
    snapshot: ShipmentEligibilitySnapshot,
    *,
    driver_id: str = "driver-42",
    window: ScheduledWindow | None = None,
) -> UUID:
    service = PickupRecoveryService(store, eligibility)
    task_id = uuid4()
    eligibility.seed(snapshot)
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=snapshot.shipment_id,
            assigned_driver_user_id=driver_id,
            assigned_batch_id=uuid4(),
            scheduled_window=window or _window(),
            created_at=_now(),
        )
    )
    return task_id


def test_api_pickup_driver_custody_blocks_all_actions_without_mutation() -> None:
    client, store, eligibility, _ = _build_client()
    shipment_id = uuid4()
    snapshot = ShipmentEligibilitySnapshot(
        shipment_id=shipment_id,
        shipment_status=ShipmentStatus.IN_CUSTODY,
        custody_started=True,
        custody_type=CustodyType.PICKUP_DRIVER,
        custody_id="driver-42",
    )

    actions: list[tuple[str, str, dict[str, Any]]] = [
        ("retry", "api-block-retry", {"reason": "blocked"}),
        (
            "reschedule",
            "api-block-reschedule",
            {
                "reason": "blocked",
                "scheduled_window": {
                    "start": (_now() + timedelta(hours=24)).isoformat(),
                    "end": (_now() + timedelta(hours=26)).isoformat(),
                },
            },
        ),
        (
            "reassign",
            "api-block-reassign",
            {"reason": "blocked", "new_driver_user_id": "driver-new"},
        ),
        ("cancel", "api-block-cancel", {"reason": "blocked"}),
    ]

    for action, idem_key, body in actions:
        task_id = _seed_task_with_snapshot(store, eligibility, snapshot)
        before = store.pickup_tasks.get_pickup_task(task_id)
        assert before is not None
        response = client.post(
            f"/pickup/tasks/{task_id}/{action}",
            headers=_auth_headers(idem_key),
            json=body,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "custody_already_started"
        after = store.pickup_tasks.get_pickup_task(task_id)
        assert after is not None
        assert after.status is PickupTaskStatus.PROOF_CAPTURED
        assert after.version == before.version
        assert after.superseded_by_task_id is None
        assert store.idempotency.get_record(idem_key) is None
        assert eligibility.get_eligibility(shipment_id) == snapshot


def test_api_non_pickup_driver_fields_do_not_block_recovery() -> None:
    client, store, eligibility, _ = _build_client()
    shipment_id = uuid4()
    task_id = _seed_task_with_snapshot(
        store,
        eligibility,
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=ShipmentStatus.IN_CUSTODY,
            custody_started=True,
            custody_type=None,
            custody_id="cust-present",
        ),
    )

    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("api-allow-non-pickup-driver"),
        json={"reason": "allowed"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "RETRY"
    assert response.json()["original_task"]["status"] == "SUPERSEDED"


def test_api_missing_shipment_eligibility_fails_closed() -> None:
    client, store, eligibility, _ = _build_client()
    service = PickupRecoveryService(store, eligibility)
    task_id = uuid4()
    shipment_id = uuid4()
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            scheduled_window=_window(),
            created_at=_now(),
        )
    )

    response = client.post(
        f"/pickup/tasks/{task_id}/retry",
        headers=_auth_headers("api-fail-closed"),
        json={"reason": "missing evidence"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "custody_already_started"
    stored = store.pickup_tasks.get_pickup_task(task_id)
    assert stored is not None
    assert stored.status is PickupTaskStatus.PROOF_CAPTURED
    assert store.idempotency.get_record("api-fail-closed") is None
