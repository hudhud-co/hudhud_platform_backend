"""Service configuration — DATABASE_URL, NATS, and ADR-0010 gates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

from shipment.domain.contract import PICKUP_ACCEPTED_DURABLE_CONSUMER


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class PersistenceBackend(StrEnum):
    POSTGRES = "postgres"
    MEMORY = "memory"


class ProductionStartupBlockedError(RuntimeError):
    """Raised when production configuration gates are not satisfied."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _optional_str(*names: str) -> str | None:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw.strip() != "":
            return raw
    return None


@dataclass(frozen=True, slots=True)
class ShipmentSettings:
    """Shipment service settings. Secret values must come from environment only."""

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "shipment"
    database_url: str | None = None
    persistence_backend: PersistenceBackend = PersistenceBackend.POSTGRES
    consumer_name: str = PICKUP_ACCEPTED_DURABLE_CONSUMER
    handler_version: str = "0.1.0"
    processing_owner: str = "shipment-worker"
    inbox_lease_seconds: int = 30
    inbox_max_attempts: int = 5
    nats_enabled: bool = False
    nats_url: str | None = None
    nats_tls_enabled: bool = False
    nats_tls_ca_file: str | None = None
    nats_user: str | None = None
    nats_password: str | None = None
    nats_token: str | None = None
    nats_creds_file: str | None = None
    allow_no_auth_local: bool = True
    pull_batch_size: int = 10
    pull_fetch_timeout_seconds: float = 5.0
    handler_concurrency: int = 4
    defer_delay_seconds: float = 5.0
    shutdown_timeout_seconds: float = 30.0
    idle_backoff_seconds: float = 0.05
    fetch_retry_backoff_seconds: float = 1.0
    adr_0010_credentials_configured: bool = False

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.adr_0010_credentials_configured:
            missing.append("adr_0010_credentials_configured")
        if missing:
            msg = f"Production startup blocked — unset gates: {', '.join(missing)}"
            raise ProductionStartupBlockedError(msg)
        if self.persistence_backend is PersistenceBackend.MEMORY:
            msg = "Production startup blocked — in-memory persistence is forbidden"
            raise ProductionStartupBlockedError(msg)


def load_settings(**overrides: object) -> ShipmentSettings:
    """Load settings from environment with optional test overrides."""
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("SHIPMENT_ENVIRONMENT", "local")))
    )
    database_url = overrides.get(
        "database_url",
        _optional_str("DATABASE_URL", "SHIPMENT_DATABASE_URL"),
    )
    if isinstance(database_url, str) and database_url.strip() == "":
        database_url = None
    resolved_url: str | None
    if database_url is None or isinstance(database_url, str):
        resolved_url = database_url
    else:
        resolved_url = str(database_url)

    nats_url = overrides.get("nats_url", _optional_str("SHIPMENT_NATS_URL"))
    if isinstance(nats_url, str) and nats_url.strip() == "":
        nats_url = None
    elif nats_url is not None and not isinstance(nats_url, str):
        nats_url = str(nats_url)

    persistence_backend = PersistenceBackend(
        str(
            overrides.get(
                "persistence_backend",
                os.environ.get("SHIPMENT_PERSISTENCE_BACKEND", PersistenceBackend.POSTGRES.value),
            )
        )
    )

    values: dict[str, object] = {
        "environment": environment,
        "service_name": str(
            overrides.get("service_name", os.environ.get("SHIPMENT_SERVICE_NAME", "shipment"))
        ),
        "database_url": resolved_url,
        "persistence_backend": persistence_backend,
        "consumer_name": str(
            overrides.get(
                "consumer_name",
                os.environ.get("SHIPMENT_CONSUMER_NAME", PICKUP_ACCEPTED_DURABLE_CONSUMER),
            )
        ),
        "handler_version": str(
            overrides.get("handler_version", os.environ.get("SHIPMENT_HANDLER_VERSION", "0.1.0"))
        ),
        "processing_owner": str(
            overrides.get(
                "processing_owner",
                os.environ.get("SHIPMENT_PROCESSING_OWNER", "shipment-worker"),
            )
        ),
        "inbox_lease_seconds": int(
            overrides.get(
                "inbox_lease_seconds",
                _env_int("SHIPMENT_INBOX_LEASE_SECONDS", 30),
            )
        ),
        "inbox_max_attempts": int(
            overrides.get(
                "inbox_max_attempts",
                _env_int("SHIPMENT_INBOX_MAX_ATTEMPTS", 5),
            )
        ),
        "nats_enabled": bool(
            overrides.get("nats_enabled", _env_bool("SHIPMENT_NATS_ENABLED", default=False))
        ),
        "nats_url": nats_url,
        "nats_tls_enabled": bool(
            overrides.get(
                "nats_tls_enabled",
                _env_bool("SHIPMENT_NATS_TLS_ENABLED", default=False),
            )
        ),
        "nats_tls_ca_file": overrides.get(
            "nats_tls_ca_file",
            _optional_str("SHIPMENT_NATS_TLS_CA_FILE"),
        ),
        "nats_user": overrides.get("nats_user", _optional_str("SHIPMENT_NATS_USER")),
        "nats_password": overrides.get("nats_password", _optional_str("SHIPMENT_NATS_PASSWORD")),
        "nats_token": overrides.get("nats_token", _optional_str("SHIPMENT_NATS_TOKEN")),
        "nats_creds_file": overrides.get(
            "nats_creds_file",
            _optional_str("SHIPMENT_NATS_CREDS_FILE"),
        ),
        "allow_no_auth_local": bool(
            overrides.get(
                "allow_no_auth_local",
                _env_bool("SHIPMENT_ALLOW_NO_AUTH_LOCAL", default=True),
            )
        ),
        "pull_batch_size": int(
            overrides.get("pull_batch_size", _env_int("SHIPMENT_PULL_BATCH_SIZE", 10))
        ),
        "pull_fetch_timeout_seconds": float(
            overrides.get(
                "pull_fetch_timeout_seconds",
                _env_float("SHIPMENT_PULL_FETCH_TIMEOUT_SECONDS", 5.0),
            )
        ),
        "handler_concurrency": int(
            overrides.get(
                "handler_concurrency",
                _env_int("SHIPMENT_HANDLER_CONCURRENCY", 4),
            )
        ),
        "defer_delay_seconds": float(
            overrides.get(
                "defer_delay_seconds",
                _env_float("SHIPMENT_DEFER_DELAY_SECONDS", 5.0),
            )
        ),
        "shutdown_timeout_seconds": float(
            overrides.get(
                "shutdown_timeout_seconds",
                _env_float("SHIPMENT_SHUTDOWN_TIMEOUT_SECONDS", 30.0),
            )
        ),
        "idle_backoff_seconds": float(
            overrides.get(
                "idle_backoff_seconds",
                _env_float("SHIPMENT_IDLE_BACKOFF_SECONDS", 0.05),
            )
        ),
        "fetch_retry_backoff_seconds": float(
            overrides.get(
                "fetch_retry_backoff_seconds",
                _env_float("SHIPMENT_FETCH_RETRY_BACKOFF_SECONDS", 1.0),
            )
        ),
        "adr_0010_credentials_configured": bool(
            overrides.get(
                "adr_0010_credentials_configured",
                _env_bool("SHIPMENT_ADR_0010_CREDENTIALS_CONFIGURED", default=False),
            )
        ),
    }
    return ShipmentSettings(**values)  # type: ignore[arg-type]
