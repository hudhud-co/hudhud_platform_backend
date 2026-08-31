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


def load_settings(**overrides: object) -> BridgeSettings:
    """Load settings with optional overrides (used in tests)."""
    return BridgeSettings(**overrides)  # type: ignore[arg-type]
