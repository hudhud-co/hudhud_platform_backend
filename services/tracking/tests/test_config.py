"""Production gate and environment configuration tests."""

from __future__ import annotations

import ssl
from unittest.mock import patch

import pytest

from tracking.config import (
    JwtSettings,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
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


def test_jwt_settings_defaults() -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    assert settings.jwt.issuer is None
    assert settings.jwt.audience is None
    assert settings.jwt.jwks_url is None
    assert settings.jwt.allowed_algorithms == ("RS256", "ES256")
    assert settings.jwt.jwks_timeout_seconds == 5.0
    assert settings.jwt.jwks_cache_ttl_seconds == 300


def test_jwt_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKING_JWT_ISSUER", "https://identity.example")
    monkeypatch.setenv("TRACKING_JWT_AUDIENCE", "tracking")
    monkeypatch.setenv("TRACKING_JWT_JWKS_URL", "https://identity.example/jwks.json")
    monkeypatch.setenv("TRACKING_JWT_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("TRACKING_JWT_JWKS_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("TRACKING_JWT_JWKS_CACHE_TTL_SECONDS", "120")
    settings = load_settings(environment=RuntimeEnvironment.LOCAL)
    assert settings.jwt.issuer == "https://identity.example"
    assert settings.jwt.audience == "tracking"
    assert settings.jwt.jwks_url == "https://identity.example/jwks.json"
    assert settings.jwt.allowed_algorithms == ("RS256",)
    assert settings.jwt.jwks_timeout_seconds == 2.5
    assert settings.jwt.jwks_cache_ttl_seconds == 120


def test_staging_blocks_incomplete_jwt_configuration() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        jwt=JwtSettings(
            issuer="https://identity.example",
            audience="tracking",
            jwks_url=None,
        ),
    )
    with pytest.raises(ProductionStartupBlockedError, match="jwt_jwks_url"):
        settings.assert_query_auth_gates()


def test_staging_blocks_non_https_jwks_url() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        jwt=JwtSettings(
            issuer="https://identity.example",
            audience="tracking",
            jwks_url="http://identity.example/jwks.json",
        ),
    )
    with pytest.raises(ProductionStartupBlockedError, match="https"):
        settings.assert_query_auth_gates()
