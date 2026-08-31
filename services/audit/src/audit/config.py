"""Service configuration — production startup blocked until explicit gates pass."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


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


@dataclass(frozen=True, slots=True)
class AuditSettings:
    """Audit service settings. Secret values must come from environment only."""

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "audit"
    database_url: str = "postgresql+psycopg://localhost/audit"
    consumer_name: str = "audit_bridge_entry_v1"
    handler_version: str = "0.1.0"
    processing_owner: str = "audit-worker"
    inbox_lease_seconds: int = 30
    inbox_max_attempts: int = 5
    nats_enabled: bool = False
    nats_url: str | None = None
    adr_0004_credentials_configured: bool = False

    def assert_production_gates(self) -> None:
        """Block production startup until ADR-0004 credentials are configured."""
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if not self.adr_0004_credentials_configured:
            msg = "Production startup blocked — unset gates: adr_0004_credentials_configured"
            raise ProductionStartupBlockedError(msg)


def load_settings(**overrides: object) -> AuditSettings:
    """Load settings from environment with optional test overrides."""
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("AUDIT_ENVIRONMENT", "local")))
    )
    nats_url = overrides.get("nats_url", os.environ.get("AUDIT_NATS_URL") or None)
    if isinstance(nats_url, str) and nats_url.strip() == "":
        nats_url = None
    values: dict[str, object] = {
        "environment": environment,
        "service_name": str(
            overrides.get("service_name", os.environ.get("AUDIT_SERVICE_NAME", "audit"))
        ),
        "database_url": str(
            overrides.get(
                "database_url",
                os.environ.get("AUDIT_DATABASE_URL", "postgresql+psycopg://localhost/audit"),
            )
        ),
        "consumer_name": str(
            overrides.get(
                "consumer_name",
                os.environ.get("AUDIT_CONSUMER_NAME", "audit_bridge_entry_v1"),
            )
        ),
        "handler_version": str(
            overrides.get("handler_version", os.environ.get("AUDIT_HANDLER_VERSION", "0.1.0"))
        ),
        "processing_owner": str(
            overrides.get(
                "processing_owner",
                os.environ.get("AUDIT_PROCESSING_OWNER", "audit-worker"),
            )
        ),
        "inbox_lease_seconds": int(
            overrides.get(
                "inbox_lease_seconds",
                _env_int("AUDIT_INBOX_LEASE_SECONDS", 30),
            )
        ),
        "inbox_max_attempts": int(
            overrides.get(
                "inbox_max_attempts",
                _env_int("AUDIT_INBOX_MAX_ATTEMPTS", 5),
            )
        ),
        "nats_enabled": bool(
            overrides.get("nats_enabled", _env_bool("AUDIT_NATS_ENABLED", default=False))
        ),
        "nats_url": nats_url,
        "adr_0004_credentials_configured": bool(
            overrides.get(
                "adr_0004_credentials_configured",
                _env_bool("AUDIT_ADR_0004_CREDENTIALS_CONFIGURED", default=False),
            )
        ),
    }
    return AuditSettings(**values)  # type: ignore[arg-type]
