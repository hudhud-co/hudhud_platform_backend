"""Production gate and environment configuration tests for Pickup relay."""

from __future__ import annotations

import pytest

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
    )
    with pytest.raises(ProductionStartupBlockedError, match="adr_0010_credentials_configured"):
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
