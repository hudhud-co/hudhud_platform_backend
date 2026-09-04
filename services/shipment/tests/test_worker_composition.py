"""Static composition-root tests for the Shipment pickup-accepted worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from shipment.config import (
    AcceptanceIngestionMode,
    PersistenceBackend,
    RuntimeEnvironment,
    load_settings,
)
from shipment.infrastructure.jetstream.deferred_transport import DeferredJetStreamTransport
from shipment.worker import WorkerStartupError, build_coordinator

WORKER_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "shipment" / "worker.py"
).read_text(encoding="utf-8")


def test_worker_source_composes_postgres_accepted_fact_store() -> None:
    assert "SqlAlchemyAcceptedFactStore" in WORKER_SOURCE
    assert "build_engine" in WORKER_SOURCE
    assert "assert_migrations_applied" in WORKER_SOURCE
    assert "owned_engine.dispose" in WORKER_SOURCE
    assert "accepted-fact store must be injected" not in WORKER_SOURCE
    assert "InMemory" not in WORKER_SOURCE
    assert "MemoryAcceptedFactStore" not in WORKER_SOURCE


def test_worker_source_does_not_mutate_topology() -> None:
    assert "add_consumer" not in WORKER_SOURCE
    assert "add_stream" not in WORKER_SOURCE
    assert "bind_existing_pull_consumer" in WORKER_SOURCE


def test_build_coordinator_rejects_compatibility_http_mode() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        acceptance_ingestion_mode=AcceptanceIngestionMode.COMPATIBILITY_HTTP,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    with pytest.raises(WorkerStartupError, match="compatibility_http"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_build_coordinator_rejects_disabled_nats_in_native_mode() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        nats_enabled=False,
    )
    with pytest.raises(WorkerStartupError, match="nats_disabled"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_build_coordinator_rejects_memory_persistence() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        persistence_backend=PersistenceBackend.MEMORY,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    with pytest.raises(WorkerStartupError, match="native_memory_persistence_forbidden"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_build_coordinator_forbids_memory_in_staging() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url="postgresql+psycopg://localhost/shipment",
        persistence_backend=PersistenceBackend.MEMORY,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
        adr_0010_credentials_configured=True,
        nats_tls_enabled=True,
        acceptance_cutover_evidence_confirmed=True,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=True,
    )
    with pytest.raises(WorkerStartupError, match="native_memory_persistence_forbidden"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())


def test_build_coordinator_requires_database_url() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        acceptance_ingestion_mode=AcceptanceIngestionMode.NATIVE_PICKUP_FACT,
        database_url=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    with pytest.raises(WorkerStartupError, match="native_database_unavailable"):
        build_coordinator(settings, transport=DeferredJetStreamTransport())
