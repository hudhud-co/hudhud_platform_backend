"""Production gate and environment configuration tests."""

from __future__ import annotations

import pytest

from tracking.config import ProductionStartupBlockedError, RuntimeEnvironment, load_settings


def test_production_blocked_without_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        adr_0010_credentials_configured=False,
    )
    with pytest.raises(ProductionStartupBlockedError, match="adr_0010_credentials_configured"):
        settings.assert_production_gates()


def test_local_env_passes_production_gates() -> None:
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    settings.assert_production_gates()


def test_nats_disabled_by_default() -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    assert settings.nats_enabled is False
    assert settings.consumer_name == "tracking_bridge_timeline_v1"
