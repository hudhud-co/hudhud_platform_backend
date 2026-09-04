"""Service configuration — DATABASE_URL and environment gates."""

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

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if not self.database_url:
            msg = "Production startup blocked — unset gates: DATABASE_URL"
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
    return ShipmentSettings(
        environment=environment,
        service_name=str(
            overrides.get("service_name", os.environ.get("SHIPMENT_SERVICE_NAME", "shipment"))
        ),
        database_url=resolved_url,
    )
