"""Customer settings."""

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
    """Production refused to start because a required gate is unset."""


@dataclass(frozen=True, slots=True)
class CustomerSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "customer"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    terms_version: str = "1.0"
    privacy_version: str = "1.0"

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("CUSTOMER_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("CUSTOMER_IDENTITY_BASE_URL")
            missing.append("CUSTOMER_IDENTITY_SERVICE_CREDENTIAL")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )


_MISSING = object()


def _resolve(overrides: dict, key: str, env_name: str, default=None):
    value = overrides.get(key, _MISSING)
    if value is not _MISSING:
        return value
    return os.environ.get(env_name, default)


def load_settings(**overrides: object) -> CustomerSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("CUSTOMER_ENVIRONMENT", "local")))
    )
    return CustomerSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "CUSTOMER_SERVICE_NAME", "customer")
        ),
        database_url=_resolve(overrides, "database_url", "CUSTOMER_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "CUSTOMER_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "CUSTOMER_IDENTITY_SERVICE_CREDENTIAL",
        ),
        terms_version=str(
            _resolve(overrides, "terms_version", "CUSTOMER_TERMS_VERSION", "1.0")
        ),
        privacy_version=str(
            _resolve(overrides, "privacy_version", "CUSTOMER_PRIVACY_VERSION", "1.0")
        ),
    )
