"""Production gate and environment configuration tests for Pickup relay."""

from __future__ import annotations

import pytest

from pickup.application.readiness import evaluate_readiness
from pickup.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)


def test_local_env_passes_production_gates() -> None:
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    settings.assert_production_gates()


def test_production_blocked_without_database() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url=None,
    )
    with pytest.raises(ProductionStartupBlockedError, match="DATABASE_URL"):
        settings.assert_production_gates()


def test_production_relay_requires_adr_0010_gate() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/pickup",
        relay_enabled=True,
        adr_0010_credentials_configured=False,
        shipment_acceptance_ingestion_mode_native_confirmed=True,
        shipment_compatibility_http_acceptance_disabled=True,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=True,
    )
    with pytest.raises(ProductionStartupBlockedError, match="adr_0010_credentials"):
        settings.assert_production_gates()


def test_production_ready_cannot_be_true() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/pickup",
        production_ready=True,
    )
    with pytest.raises(ProductionStartupBlockedError, match="production_ready"):
        settings.assert_production_gates()


def test_relay_disabled_by_default() -> None:
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    assert settings.relay_enabled is False


def test_relay_configuration_valid_local_no_auth() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_dev_no_auth=True,
    )
    assert settings.relay_configuration_valid()


def test_staging_relay_requires_cutover_configuration_gates() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_user="pickup",
        nats_password="not-a-real-secret",
        adr_0010_credentials_configured=True,
        shipment_acceptance_ingestion_mode_native_confirmed=False,
        shipment_compatibility_http_acceptance_disabled=False,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=False,
    )
    blockers = settings.relay_cutover_gate_blockers()
    assert "shipment_native_pickup_fact_mode_not_confirmed" in blockers
    assert "shipment_compatibility_http_acceptance_not_disabled" in blockers
    assert "legacy_writer_revocation_not_externally_confirmed" in blockers
    assert settings.relay_configuration_valid() is False

    report = evaluate_readiness(
        settings=settings,
        engine=None,
        persistence_wired=True,
        authorization_configured=True,
        shipment_eligibility_configured=True,
        nats_reachable=True,
    )
    assert report.ready is False
    assert "shipment_native_pickup_fact_mode_not_confirmed" in report.blockers
    assert "legacy_writer_revocation_not_externally_confirmed" in report.blockers
    assert report.checks["production_ready_false"] is True


def test_staging_relay_passes_when_cutover_gates_configured() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        relay_enabled=True,
        nats_url="nats://localhost:4222",
        nats_user="pickup",
        nats_password="not-a-real-secret",
        adr_0010_credentials_configured=True,
        shipment_acceptance_ingestion_mode_native_confirmed=True,
        shipment_compatibility_http_acceptance_disabled=True,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=True,
    )
    assert settings.relay_cutover_gate_blockers() == []
    assert settings.relay_configuration_valid() is True


def test_production_relay_requires_full_cutover_evidence_config() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        database_url="postgresql+psycopg://localhost/pickup",
        relay_enabled=True,
        adr_0010_credentials_configured=True,
        nats_tls_enabled=True,
        nats_url="nats://broker.example:4222",
        nats_creds_file="/run/secrets/pickup.creds",
        shipment_acceptance_ingestion_mode_native_confirmed=False,
        shipment_compatibility_http_acceptance_disabled=True,
        legacy_pickup_acceptance_writer_revocation_externally_confirmed=True,
    )
    with pytest.raises(
        ProductionStartupBlockedError,
        match="shipment_native_pickup_fact_mode_not_confirmed",
    ):
        settings.assert_production_gates()
