"""Pickup recovery HTTP + PostgreSQL proof (ASGI TestClient, real commits)."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pickup.application.recovery_service import PickupRecoveryService, RegisterPickupTaskCommand
from pickup.config import RuntimeEnvironment, load_settings
from pickup.domain.value_objects import CustodyType, ShipmentStatus
from pickup.infrastructure.authorizers.default_deny import DefaultDenyRecoveryAuthorizer
from pickup.infrastructure.authorizers.fake import FakeRecoveryAuthorizer
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyRecoveryUnitOfWork
from pickup.infrastructure.unavailable_shipment_eligibility import (
    UnavailableShipmentEligibilityAdapter,
)
from pickup.main import create_app
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot
from sqlalchemy import create_engine, text

SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"


def _count(database_url: str, sql: str, params: dict[str, object] | None = None) -> int:
    engine = create_engine(database_url, future=True)
    with engine.connect() as connection:
        value = connection.execute(text(sql), params or {}).scalar_one()
    engine.dispose()
    return int(value)


def _seed_task(
    service: PickupRecoveryService,
    eligibility: InMemoryShipmentEligibilityAdapter,
    *,
    custody_started: bool = False,
) -> UUID:
    now = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    shipment_id = uuid4()
    task_id = uuid4()
    eligibility.seed(
        ShipmentEligibilitySnapshot(
            shipment_id=shipment_id,
            shipment_status=(
                ShipmentStatus.IN_CUSTODY if custody_started else ShipmentStatus.CREATED
            ),
            custody_started=custody_started,
            custody_type=CustodyType.PICKUP_DRIVER if custody_started else None,
            custody_id="driver-42" if custody_started else None,
        )
    )
    service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=task_id,
            shipment_id=shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            created_at=now,
        )
    )
    return task_id


def _build_client(
    database_url: str,
    uow: SqlAlchemyRecoveryUnitOfWork,
    eligibility: InMemoryShipmentEligibilityAdapter | UnavailableShipmentEligibilityAdapter,
    authorizer: FakeRecoveryAuthorizer | DefaultDenyRecoveryAuthorizer,
) -> TestClient:
    settings = load_settings(environment=RuntimeEnvironment.TEST, database_url=database_url)
    app = create_app(
        settings,
        unit_of_work=uow,
        shipment_eligibility=eligibility,
        recovery_authorizer=authorizer,
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SENSITIVE_TOKEN}",
        "Idempotency-Key": idempotency_key,
    }


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    try:
        engine = build_engine(database_url)
        uow = SqlAlchemyRecoveryUnitOfWork(build_session_factory(engine))
        eligibility = InMemoryShipmentEligibilityAdapter(production_ready=True)
        authorizer = FakeRecoveryAuthorizer(production_ready=True)
        service = PickupRecoveryService(uow, eligibility)
        client = _build_client(database_url, uow, eligibility, authorizer)

        task_id = _seed_task(service, eligibility)
        before_tasks = _count(database_url, "SELECT COUNT(*) FROM pickup_tasks")
        before_history = _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_history")
        before_idem = _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_idempotency")

        retry = client.post(
            f"/pickup/tasks/{task_id}/retry",
            headers=_auth_headers("http-pg-retry-1"),
            json={"reason": "customer unavailable"},
        )
        retry_committed = retry.status_code == 200 and retry.json()["idempotent_replay"] is False
        retry_body = retry.json() if retry.status_code == 200 else {}
        replacement_id = (
            retry_body["replacement_task"]["pickup_task_id"]
            if retry_committed and retry_body.get("replacement_task")
            else None
        )
        supersession_persisted = (
            retry_committed
            and replacement_id is not None
            and _count(database_url, "SELECT COUNT(*) FROM pickup_tasks") == before_tasks + 1
            and _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_history")
            == before_history + 1
            and _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_idempotency")
            == before_idem + 1
            and retry_body["original_task"]["status"] == "SUPERSEDED"
            and retry_body["replacement_task"]["attempt_number"] == 2
        )

        after_retry_tasks = _count(database_url, "SELECT COUNT(*) FROM pickup_tasks")
        replay = client.post(
            f"/pickup/tasks/{task_id}/retry",
            headers=_auth_headers("http-pg-retry-1"),
            json={"reason": "customer unavailable"},
        )
        idempotent_replay = (
            replay.status_code == 200
            and replay.json()["idempotent_replay"] is True
            and _count(database_url, "SELECT COUNT(*) FROM pickup_tasks") == after_retry_tasks
        )

        conflict = client.post(
            f"/pickup/tasks/{task_id}/cancel",
            headers=_auth_headers("http-pg-retry-1"),
            json={"reason": "cancel with reused key"},
        )
        conflicting_key = (
            conflict.status_code == 409
            and conflict.json()["detail"]["code"] == "conflicting_idempotency_key"
        )

        cancel_task = _seed_task(service, eligibility)
        before_cancel_tasks = _count(database_url, "SELECT COUNT(*) FROM pickup_tasks")
        cancel = client.post(
            f"/pickup/tasks/{cancel_task}/cancel",
            headers=_auth_headers("http-pg-cancel-1"),
            json={"reason": "customer cancelled"},
        )
        cancel_without_replacement = (
            cancel.status_code == 200
            and cancel.json()["replacement_task"] is None
            and cancel.json()["original_task"]["status"] == "CANCELLED"
            and _count(database_url, "SELECT COUNT(*) FROM pickup_tasks") == before_cancel_tasks
        )

        custody_task = _seed_task(service, eligibility, custody_started=True)
        before_custody_tasks = _count(database_url, "SELECT COUNT(*) FROM pickup_tasks")
        before_custody_history = _count(
            database_url, "SELECT COUNT(*) FROM pickup_recovery_history"
        )
        before_custody_idem = _count(
            database_url, "SELECT COUNT(*) FROM pickup_recovery_idempotency"
        )
        custody_blocked = client.post(
            f"/pickup/tasks/{custody_task}/retry",
            headers=_auth_headers("http-pg-custody-1"),
            json={"reason": "should fail"},
        )
        custody_no_mutation = (
            custody_blocked.status_code == 409
            and _count(database_url, "SELECT COUNT(*) FROM pickup_tasks") == before_custody_tasks
            and _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_history")
            == before_custody_history
            and _count(database_url, "SELECT COUNT(*) FROM pickup_recovery_idempotency")
            == before_custody_idem
        )

        unauth_task = _seed_task(service, eligibility)
        unauthenticated = client.post(
            f"/pickup/tasks/{unauth_task}/retry",
            headers={"Idempotency-Key": "http-pg-unauth"},
            json={"reason": "no auth"},
        )
        unauthenticated_401 = unauthenticated.status_code == 401

        spoof_authorizer = FakeRecoveryAuthorizer(production_ready=True, actor_id="from-token")
        spoof_client = _build_client(database_url, uow, eligibility, spoof_authorizer)
        spoof_task = _seed_task(service, eligibility)
        spoofed = spoof_client.post(
            f"/pickup/tasks/{spoof_task}/retry",
            headers={
                **_auth_headers("http-pg-spoof"),
                "X-User-Id": "spoofed-user",
                "X-Actor-Id": "spoofed-actor",
                "X-Role": "admin",
            },
            json={"reason": "retry", "actor_id": "body-spoof"},
        )
        identity_headers_ignored = (
            spoofed.status_code == 200
            and spoof_authorizer.seen_tokens == [SENSITIVE_TOKEN]
            and "spoofed-user" not in spoof_authorizer.seen_tokens
        )

        health = client.get("/health")
        health_liveness = health.status_code == 200 and health.json() == {
            "status": "ok",
            "service": "pickup",
        }

        deny_settings = load_settings(
            environment=RuntimeEnvironment.TEST,
            database_url=database_url,
        )
        deny_app = create_app(
            deny_settings,
            unit_of_work=uow,
            shipment_eligibility=UnavailableShipmentEligibilityAdapter(),
            recovery_authorizer=DefaultDenyRecoveryAuthorizer(),
        )
        deny_client = TestClient(deny_app, raise_server_exceptions=False)
        ready = deny_client.get("/ready")
        blockers = ready.json().get("blockers", [])
        ready_reports_blockers = (
            ready.status_code == 503
            and "authorization_adapter_not_configured" in blockers
            and "shipment_eligibility_adapter_deferred" in blockers
            and deny_client.get("/health").json()["status"] == "ok"
        )

        engine.dispose()
        payload = {
            "retry_supersession_persisted": supersession_persisted,
            "idempotent_replay": idempotent_replay,
            "conflicting_key": conflicting_key,
            "cancel_without_replacement": cancel_without_replacement,
            "custody_no_mutation": custody_no_mutation,
            "unauthenticated_401": unauthenticated_401,
            "identity_headers_ignored": identity_headers_ignored,
            "health_liveness": health_liveness,
            "ready_reports_blockers": ready_reports_blockers,
        }
        print(json.dumps(payload))
        return 0
    except Exception as exc:  # noqa: BLE001 — probe entrypoint reports failure
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
