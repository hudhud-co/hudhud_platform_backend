"""Acceptance ingestion mode cutover-guard tests (fakes only)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RegisterPickupTaskCommand,
)
from shipment.application.readiness import evaluate_readiness
from shipment.config import (
    AcceptanceIngestionMode,
    PersistenceBackend,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
    parse_acceptance_ingestion_mode,
)
from shipment.domain.contract import PICKUP_ACCEPTED_DURABLE_CONSUMER
from shipment.domain.value_objects import AcceptanceOutcome
from shipment.infrastructure.authorizers.fake import FakeAcceptanceAuthorizer
from shipment.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from shipment.infrastructure.memory import InMemoryAcceptanceUnitOfWork
from shipment.main import create_app
from shipment.worker import WorkerStartupError, build_coordinator

SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"
SENSITIVE_SCAN = "WB-SECRET-SCAN-ID"


async def _seed(store: InMemoryAcceptanceUnitOfWork):
    service = AcceptanceLifecycleService(store)
    _order, shipment = await service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number=SENSITIVE_SCAN,
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    pickup_task_id = uuid4()
    await service.register_pickup_task(
        RegisterPickupTaskCommand(
            pickup_task_id=pickup_task_id,
            shipment_id=shipment.shipment_id,
            assigned_driver_user_id="driver-42",
            assigned_batch_id=uuid4(),
            has_pickup_condition_proof=True,
        )
    )
    return shipment, pickup_task_id


def _seed_sync(store: InMemoryAcceptanceUnitOfWork):
    return asyncio.run(_seed(store))


def _client(store: InMemoryAcceptanceUnitOfWork, **settings_overrides: object) -> TestClient:
    settings = load_settings(environment=RuntimeEnvironment.TEST, **settings_overrides)
    app = create_app(
        settings,
        unit_of_work=store,
        acceptance_authorizer=FakeAcceptanceAuthorizer(),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_parse_acceptance_ingestion_mode_enum_and_invalid() -> None:
    assert (
        parse_acceptance_ingestion_mode("compatibility_http")
        is AcceptanceIngestionMode.COMPATIBILITY_HTTP
    )
    assert (
        parse_acceptance_ingestion_mode(AcceptanceIngestionMode.NATIVE_PICKUP_FACT)
        is AcceptanceIngestionMode.NATIVE_PICKUP_FACT
    )
    with pytest.raises(ValueError, match="invalid acceptance_ingestion_mode"):
        parse_acceptance_ingestion_mode("both")
    with pytest.raises(ValueError, match="invalid acceptance_ingestion_mode"):
        parse_acceptance_ingestion_mode("http_and_native")


def test_local_and_test_default_to_compatibility_http() -> None:
    local = load_settings(environment=RuntimeEnvironment.LOCAL)
    test = load_settings(environment=RuntimeEnvironment.TEST)
    assert local.acceptance_ingestion_mode is AcceptanceIngestionMode.COMPATIBILITY_HTTP
    assert test.acceptance_ingestion_mode is AcceptanceIngestionMode.COMPATIBILITY_HTTP
    assert local.http_acceptance_enabled()
    assert not local.native_pickup_fact_enabled()


def test_staging_and_production_fail_closed_without_explicit_mode() -> None:
    staging = load_settings(environment=RuntimeEnvironment.STAGING)
    production = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/shipment",
        adr_0010_credentials_configured=True,
    )
    assert staging.acceptance_ingestion_mode is None
    assert production.acceptance_ingestion_mode is None
    with pytest.raises(ProductionStartupBlockedError, match="acceptance_ingestion_mode"):
        staging.assert_production_gates()
    with pytest.raises(ProductionStartupBlockedError, match="acceptance_ingestion_mode"):
        production.assert_production_gates()

    report = evaluate_readiness(
        settings=staging,
        engine=None,
        persistence_wired=True,
        authorization_adapter_ready=True,
    )
    assert report.ready is False
    assert "acceptance_ingestion_mode_missing" in report.blockers


def test_compatibility_mode_enables_http_and_blocks_native_worker() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(
        store,
        acceptance_ingestion_mode=AcceptanceIngestionMode.COMPATIBILITY_HTTP,
    )
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": SENSITIVE_SCAN,
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": AcceptanceOutcome.ACCEPTED.value,
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "compat-ok",
        },
    )
    assert response.status_code == 200

    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        acceptance_ingestion_mode=AcceptanceIngestionMode.COMPATIBILITY_HTTP,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    assert settings.native_worker_startup_blockers() == (
        "native_worker_blocked_by_compatibility_http_mode",
    )
    with pytest.raises(WorkerStartupError, match="compatibility_http"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_native_mode_blocks_http_before_uow_without_mutation() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    authorizer = FakeAcceptanceAuthorizer()
    events_before = len(store._shipment_events)  # noqa: SLF001
    idem_before = len(store._idempotency)  # noqa: SLF001
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
    )
    app = create_app(settings, unit_of_work=store, acceptance_authorizer=authorizer)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": SENSITIVE_SCAN,
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": AcceptanceOutcome.ACCEPTED.value,
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "native-block",
        },
    )
    assert response.status_code == 503
    body = response.json()
    assert body["detail"].startswith("compatibility HTTP acceptance disabled")
    assert SENSITIVE_TOKEN not in response.text
    assert SENSITIVE_SCAN not in response.text
    assert "native-block" not in response.text
    assert authorizer.seen_tokens == []
    assert len(store._shipment_events) == events_before  # noqa: SLF001
    assert len(store._idempotency) == idem_before  # noqa: SLF001
    assert store._shipments[shipment.shipment_id].current_status.value == "CREATED"  # noqa: SLF001


def test_disabled_mode_blocks_http_and_native_worker() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    shipment, pickup_task_id = _seed_sync(store)
    client = _client(
        store,
        acceptance_ingestion_mode=AcceptanceIngestionMode.DISABLED,
    )
    response = client.post(
        f"/v1/shipments/{shipment.shipment_id}/acceptance-scans",
        json={
            "pickup_task_id": str(pickup_task_id),
            "scanned_identifier": SENSITIVE_SCAN,
            "scan_timestamp": "2026-05-31T10:00:00+00:00",
            "outcome": AcceptanceOutcome.ACCEPTED.value,
        },
        headers={
            "Authorization": f"Bearer {SENSITIVE_TOKEN}",
            "Idempotency-Key": "disabled-block",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "acceptance ingestion is disabled"

    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        acceptance_ingestion_mode=AcceptanceIngestionMode.DISABLED,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    assert "acceptance_ingestion_disabled" in settings.native_worker_startup_blockers()
    with pytest.raises(WorkerStartupError, match="acceptance_ingestion_disabled"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_native_mode_worker_requires_all_injected_gates() -> None:
    incomplete = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=False,
        nats_tls_enabled=False,
        acceptance_cutover_evidence_confirmed=False,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=False,
    )
    blockers = incomplete.native_worker_startup_blockers()
    assert "adr_0010_credentials_gate_missing" in blockers
    assert "adr_0010_tls_gate_missing" in blockers
    assert "cutover_evidence_gate_missing" in blockers
    assert "legacy_writer_revocation_not_externally_confirmed" in blockers

    complete = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://broker.example:4222",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=True,
        acceptance_cutover_evidence_confirmed=True,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=True,
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        persistence_backend=PersistenceBackend.POSTGRES,
    )
    assert complete.native_worker_startup_blockers() == ()


def test_no_boolean_combination_enables_both_paths() -> None:
    """Closed enum: HTTP and native cannot both be enabled."""
    for mode in AcceptanceIngestionMode:
        settings = load_settings(
            environment=RuntimeEnvironment.TEST,
            acceptance_ingestion_mode=mode,
            nats_enabled=True,
        )
        both = settings.http_acceptance_enabled() and settings.native_pickup_fact_enabled()
        assert both is False


def test_readiness_blockers_for_native_and_disabled() -> None:
    disabled = load_settings(
        environment=RuntimeEnvironment.TEST,
        acceptance_ingestion_mode=AcceptanceIngestionMode.DISABLED,
    )
    disabled_report = evaluate_readiness(
        settings=disabled,
        engine=None,
        persistence_wired=True,
        authorization_adapter_ready=True,
    )
    assert disabled_report.ready is False
    assert "acceptance_ingestion_disabled" in disabled_report.blockers
    assert disabled_report.checks["production_ready_false"] is True

    native = load_settings(
        environment=RuntimeEnvironment.STAGING,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://localhost:4222",
        consumer_name="wrong_durable",
        adr_0010_credentials_configured=False,
        nats_tls_enabled=False,
        acceptance_cutover_evidence_confirmed=False,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=False,
    )
    native_report = evaluate_readiness(
        settings=native,
        engine=None,
        persistence_wired=True,
        authorization_adapter_ready=True,
        nats_reachable=True,
        nats_binding_verified=False,
        migrations_applied=False,
    )
    assert native_report.ready is False
    assert "native_durable_binding_missing" in native_report.blockers
    assert "native_migrations_unavailable" in native_report.blockers
    assert "adr_0010_credential_tls_gate_missing" in native_report.blockers
    assert "cutover_evidence_gate_missing" in native_report.blockers
    assert "legacy_writer_revocation_not_externally_confirmed" in native_report.blockers
    assert "native_durable_binding_unverified" in native_report.blockers


def test_health_remains_liveness_when_ingestion_disabled() -> None:
    store = InMemoryAcceptanceUnitOfWork()
    client = _client(
        store,
        acceptance_ingestion_mode=AcceptanceIngestionMode.DISABLED,
    )
    assert client.get("/health").json() == {"status": "ok", "service": "shipment"}
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert "acceptance_ingestion_disabled" in ready.json()["blockers"]
