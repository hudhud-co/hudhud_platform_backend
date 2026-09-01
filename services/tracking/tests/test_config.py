"""Production gate and environment configuration tests."""

from __future__ import annotations

import ssl
from unittest.mock import patch

import pytest

from tracking.config import ProductionStartupBlockedError, RuntimeEnvironment, load_settings
from tracking.infrastructure.jetstream.connection import (
    NatsAuthRequiredError,
    build_nats_connect_options,
)


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


def test_creds_file_passed_as_user_credentials(tmp_path) -> None:
    creds = tmp_path / "tracking.creds"
    creds.write_text("placeholder-creds-content", encoding="utf-8")
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        nats_url="tls://localhost:4222",
        nats_creds_file=str(creds),
        nats_tls_enabled=True,
        allow_no_auth_local=True,
    )
    fake_context = ssl.create_default_context()
    with patch(
        "tracking.infrastructure.jetstream.connection.ssl.create_default_context",
        return_value=fake_context,
    ):
        options = build_nats_connect_options(settings)
    assert options["user_credentials"] == str(creds)
    assert options["tls"] is fake_context


def test_tls_ca_file_builds_verifying_context(tmp_path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("generated-ca-bundle", encoding="utf-8")
    settings = load_settings(
        environment=RuntimeEnvironment.LOCAL,
        nats_url="tls://localhost:4222",
        allow_no_auth_local=True,
        nats_tls_enabled=True,
        nats_tls_ca_file=str(ca_file),
    )
    fake_context = ssl.create_default_context()
    with patch(
        "tracking.infrastructure.jetstream.connection.ssl.create_default_context",
        return_value=fake_context,
    ) as create_context:
        options = build_nats_connect_options(settings)
    create_context.assert_called_once_with(cafile=str(ca_file))
    assert options["tls"] is fake_context
    assert fake_context.verify_mode == ssl.CERT_REQUIRED


def test_production_requires_tls_and_credentials() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        nats_url="tls://broker.example:4222",
        adr_0010_credentials_configured=False,
        nats_tls_enabled=True,
    )
    with pytest.raises(NatsAuthRequiredError, match="ADR-0010"):
        build_nats_connect_options(settings)
