"""Production gate and environment configuration tests."""

from __future__ import annotations

import pytest

from legacy_event_bridge.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)


def test_production_blocked_without_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        adr_0004_credentials_configured=False,
        adr_0007_staging_gates_satisfied=True,
    )
    with pytest.raises(ProductionStartupBlockedError, match="adr_0004_credentials_configured"):
        settings.assert_production_gates()


def test_production_blocked_without_staging_gates() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        adr_0004_credentials_configured=True,
        adr_0007_staging_gates_satisfied=False,
    )
    with pytest.raises(ProductionStartupBlockedError, match="adr_0007_staging_gates_satisfied"):
        settings.assert_production_gates()


def test_local_env_passes_production_gates() -> None:
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    settings.assert_production_gates()
