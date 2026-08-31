"""Service configuration — production startup blocked until explicit gates pass."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ProductionStartupBlockedError(RuntimeError):
    """Raised when production configuration gates are not satisfied."""


class BridgeSettings(BaseSettings):
    """Bridge service settings. Secret values must come from environment only."""

    model_config = SettingsConfigDict(
        env_prefix="LEGACY_BRIDGE_",
        env_file=".env",
        extra="ignore",
    )

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "legacy_event_bridge"
    database_url: str = Field(
        default="postgresql+psycopg://localhost/legacy_event_bridge",
        description="Bridge-owned database URL (names only in docs; value from env).",
    )
    capture_source: str = "legacy_primary_slot"
    mapper_version: str = "1.0.0"
    source_system: str = "legacy"
    outbox_max_attempts: int = 5
    outbox_lease_seconds: int = 30

    adr_0004_credentials_configured: bool = False
    adr_0007_staging_gates_satisfied: bool = False

    relay_enabled: bool = False
    relay_owner_id: str = "bridge-relay"
    relay_batch_size: int = 50
    relay_poll_interval_seconds: float = 1.0
    relay_publish_timeout_seconds: float = 5.0

    nats_url: str | None = None
    nats_dev_no_auth: bool = False
    nats_user: str | None = None
    nats_password: str | None = None
    nats_token: str | None = None
    nats_tls_enabled: bool = False
    nats_tls_ca_file: str | None = None
    nats_connect_timeout_seconds: float = 5.0

    def assert_production_gates(self) -> None:
        """Block production startup until ADR-0004 and ADR-0007 gates are satisfied."""
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.adr_0004_credentials_configured:
            missing.append("adr_0004_credentials_configured")
        if not self.adr_0007_staging_gates_satisfied:
            missing.append("adr_0007_staging_gates_satisfied")
        if missing:
            joined = ", ".join(missing)
            msg = f"Production startup blocked — unset gates: {joined}"
            raise ProductionStartupBlockedError(msg)

    def relay_configuration_valid(self) -> bool:
        """Return True when relay NATS settings are internally consistent."""
        if not self.relay_enabled:
            return True
        if not self.nats_url:
            return False
        has_credentials = bool(self.nats_token or (self.nats_user and self.nats_password))
        if self.nats_dev_no_auth:
            return self.environment is not RuntimeEnvironment.PRODUCTION
        if self.environment is RuntimeEnvironment.PRODUCTION:
            return (
                self.adr_0004_credentials_configured
                and self.nats_tls_enabled
                and has_credentials
            )
        return has_credentials


def load_settings(**overrides: object) -> BridgeSettings:
    """Load settings with optional overrides (used in tests)."""
    return BridgeSettings(**overrides)  # type: ignore[arg-type]
