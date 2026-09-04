"""Shipment acceptance HTTP + PostgreSQL proof (HTTPX ASGI, real commits)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RegisterPickupTaskCommand,
)
from shipment.config import RuntimeEnvironment, load_settings
from shipment.infrastructure.authorizers.default_deny import DefaultDenyAcceptanceAuthorizer
from shipment.infrastructure.authorizers.fake import FakeAcceptanceAuthorizer
from shipment.infrastructure.persistence.acceptance_uow import SqlAlchemyAcceptanceUnitOfWork
from shipment.infrastructure.persistence.session import (
    build_async_engine,
    build_async_session_factory,
)
from shipment.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"


def _sync_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


def _count(database_url: str, sql: str, params: dict[str, object] | None = None) -> int:
    engine = create_engine(_sync_url(database_url), future=True)
    with engine.connect() as connection:
        value = connection.execute(text(sql), params or {}).scalar_one()
    engine.dispose()
    return int(value)


async def _seed(
    service: AcceptanceLifecycleService,
    *,
    waybill: str,
    driver_id: str = "driver-42",
) -> tuple[object, object]:
    now = datetime(2026, 5, 31, 9, 0, tzinfo=UTC)
    _, shipment = await service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number=waybill,
            created_at=now,
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


def _build_app(
    database_url: str,
    uow: SqlAlchemyAcceptanceUnitOfWork,
    *,
    authorizer: FakeAcceptanceAuthorizer | DefaultDenyAcceptanceAuthorizer,
    environment: RuntimeEnvironment = RuntimeEnvironment.TEST,
):
    settings = load_settings(environment=environment, database_url=database_url)
    return create_app(
        settings,
        unit_of_work=uow,
        acceptance_authorizer=authorizer,
    )


async def _client_for(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _run() -> dict[str, object]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    engine: AsyncEngine = build_async_engine(database_url)
    uow = SqlAlchemyAcceptanceUnitOfWork(build_async_session_factory(engine))
    service = AcceptanceLifecycleService(uow)
    authorizer = FakeAcceptanceAuthorizer(actor_user_id="driver-42")
    app = _build_app(database_url, uow, authorizer=authorizer)

    async with await _client_for(app) as client:
        shipment, pickup_task_id = await _seed(service, waybill="WB-HTTP-PROOF-1")
        payload = {
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": "WB-HTTP-PROOF-1",
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": "accepted",
        }
        headers = {
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "http-pg-acceptance-1",
        }

        first = await client.post(
            f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
            json=payload,
            headers=headers,
        )
        acceptance_committed = (
            first.status_code == 200 and first.json()["idempotent_replay"] is False
        )
        body = first.json() if first.status_code == 200 else {}

        shipment_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM shipments WHERE shipment_id = :id",
            {"id": shipment.shipment_id},
        )
        pickup_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM pickup_task_snapshots WHERE pickup_task_id = :id",
            {"id": pickup_task_id},
        )
        decision_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM acceptance_decisions WHERE shipment_id = :id",
            {"id": shipment.shipment_id},
        )
        event_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM shipment_events WHERE shipment_id = :id",
            {"id": shipment.shipment_id},
        )
        audit_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM acceptance_audit_logs WHERE entity_id = :id",
            {"id": str(shipment.shipment_id)},
        )
        idem_rows = _count(
            database_url,
            "SELECT COUNT(*) FROM acceptance_idempotency WHERE idempotency_key = :key",
            {"key": "http-pg-acceptance-1"},
        )
        rows_persisted = (
            shipment_rows == 1
            and pickup_rows == 1
            and decision_rows == 1
            and event_rows == 1
            and audit_rows == 1
            and idem_rows == 1
            and body.get("current_status") == "IN_CUSTODY"
        )

        replay = await client.post(
            f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
            json=payload,
            headers=headers,
        )
        idempotent_replay = (
            replay.status_code == 200
            and replay.json()["idempotent_replay"] is True
            and _count(
                database_url,
                "SELECT COUNT(*) FROM acceptance_idempotency WHERE idempotency_key = :key",
                {"key": "http-pg-acceptance-1"},
            )
            == 1
        )

        conflict = await client.post(
            f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
            json={**payload, "scanned_identifier": "WB-OTHER"},
            headers=headers,
        )
        conflicting_key = conflict.status_code == 409

        bad_shipment, bad_pickup = await _seed(service, waybill="WB-HTTP-PROOF-BAD")
        before_decisions = _count(database_url, "SELECT COUNT(*) FROM acceptance_decisions")
        before_events = _count(database_url, "SELECT COUNT(*) FROM shipment_events")
        before_audits = _count(database_url, "SELECT COUNT(*) FROM acceptance_audit_logs")
        before_idem = _count(database_url, "SELECT COUNT(*) FROM acceptance_idempotency")
        invalid = await client.post(
            f"/v1/shipments/{bad_shipment.shipment_id}/acceptance-scans",
            json={
                "pickup_task_id": str(bad_pickup),
                "scanned_identifier": "WRONG-WAYBILL",
                "scan_timestamp": "2026-05-31T10:00:00+00:00",
                "outcome": "accepted",
            },
            headers={
                "Authorization": f"Bearer {SENSITIVE_TOKEN}",
                "Idempotency-Key": "http-pg-acceptance-invalid",
            },
        )
        no_partial_on_invalid = (
            invalid.status_code == 422
            and _count(database_url, "SELECT COUNT(*) FROM acceptance_decisions")
            == before_decisions
            and _count(database_url, "SELECT COUNT(*) FROM shipment_events") == before_events
            and _count(database_url, "SELECT COUNT(*) FROM acceptance_audit_logs")
            == before_audits
            and _count(database_url, "SELECT COUNT(*) FROM acceptance_idempotency")
            == before_idem
        )

        unauth_shipment, unauth_pickup = await _seed(service, waybill="WB-HTTP-PROOF-UNAUTH")
        unauthenticated = await client.post(
            f"/v1/shipments/{unauth_shipment.shipment_id}/acceptance-scans",
            json={
                "pickup_task_id": str(unauth_pickup),
                "scanned_identifier": "WB-HTTP-PROOF-UNAUTH",
                "scan_timestamp": "2026-05-31T10:00:00+00:00",
                "outcome": "accepted",
            },
            headers={"Idempotency-Key": "http-pg-acceptance-unauth"},
        )
        unauthenticated_401 = unauthenticated.status_code == 401

        spoof_shipment, spoof_pickup = await _seed(
            service, waybill="WB-HTTP-PROOF-SPOOF", driver_id="driver-from-auth"
        )
        spoof_authorizer = FakeAcceptanceAuthorizer(actor_user_id="driver-from-auth")
        spoof_app = _build_app(database_url, uow, authorizer=spoof_authorizer)
        async with await _client_for(spoof_app) as spoof_client:
            spoofed = await spoof_client.post(
                f"/v1/shipments/{spoof_shipment.shipment_id}/acceptance-scans",
                json={
                    "pickup_task_id": str(spoof_pickup),
                    "scanned_identifier": "WB-HTTP-PROOF-SPOOF",
                    "scan_timestamp": "2026-05-31T10:00:00+00:00",
                    "outcome": "accepted",
                },
                headers={
                    "Authorization": f"Bearer {SENSITIVE_TOKEN}",
                    "Idempotency-Key": "http-pg-acceptance-spoof",
                    "X-User-Id": "attacker",
                    "X-Role": "admin",
                },
            )
        identity_headers_ignored = (
            spoofed.status_code == 200
            and spoofed.json()["current_custody_id"] == "driver-from-auth"
            and spoof_authorizer.seen_tokens == [SENSITIVE_TOKEN]
        )

        health = await client.get("/health")
        health_liveness = health.status_code == 200 and health.json() == {
            "status": "ok",
            "service": "shipment",
        }

    deny_app = _build_app(
        database_url,
        uow,
        authorizer=DefaultDenyAcceptanceAuthorizer(),
        environment=RuntimeEnvironment.STAGING,
    )
    async with await _client_for(deny_app) as deny_client:
        ready = await deny_client.get("/ready")
        health_ok = await deny_client.get("/health")
    ready_reports_blockers = (
        ready.status_code == 503
        and "authorization_adapter_not_ready" in ready.json().get("blockers", [])
        and health_ok.json()["status"] == "ok"
    )

    await engine.dispose()
    return {
        "acceptance_committed": acceptance_committed,
        "rows_persisted": rows_persisted,
        "idempotent_replay": idempotent_replay,
        "conflicting_key": conflicting_key,
        "no_partial_on_invalid": no_partial_on_invalid,
        "unauthenticated_401": unauthenticated_401,
        "identity_headers_ignored": identity_headers_ignored,
        "health_liveness": health_liveness,
        "ready_reports_blockers": ready_reports_blockers,
    }


def main() -> int:
    try:
        payload = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — probe entrypoint reports failure
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
