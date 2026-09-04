"""Service configuration — DATABASE_URL and runtime environment only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


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


def _optional_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw


@dataclass(frozen=True, slots=True)
class PickupSettings:
    """Pickup service settings. Secret values must come from environment only."""

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "pickup"
    database_url: str | None = None
    persistence_backend: PersistenceBackend = PersistenceBackend.POSTGRES

    def assert_production_gates(self) -> None:
        """Block production startup when persistence is unsafe."""
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if self.persistence_backend is PersistenceBackend.MEMORY:
            msg = "Production startup blocked — in-memory persistence is forbidden"
            raise ProductionStartupBlockedError(msg)
        if not self.database_url:
            msg = "Production startup blocked — unset gates: DATABASE_URL"
            raise ProductionStartupBlockedError(msg)


def load_settings(**overrides: object) -> PickupSettings:
    """Load settings from environment with optional test overrides."""
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("PICKUP_ENVIRONMENT", "local")))
    )

    if "database_url" in overrides:
        raw_url = overrides["database_url"]
        database_url = None if raw_url is None or str(raw_url).strip() == "" else str(raw_url)
    else:
        database_url = _optional_str("DATABASE_URL") or _optional_str("PICKUP_DATABASE_URL")

    persistence_backend = PersistenceBackend(
        str(
            overrides.get(
                "persistence_backend",
                os.environ.get("PICKUP_PERSISTENCE_BACKEND", PersistenceBackend.POSTGRES.value),
            )
        )
    )
    return PickupSettings(
        environment=environment,
        service_name=str(
            overrides.get("service_name", os.environ.get("PICKUP_SERVICE_NAME", "pickup"))
        ),
        database_url=database_url,
        persistence_backend=persistence_backend,
    )
