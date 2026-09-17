"""Ordering settings.

Two settings are deliberately empty by default and fail closed when left that way:

* ``serviceable_governorates`` — where Hudhud delivers is operational, not a constant;
* the delivery tariff, which lives in the database because v6.3 publishes no rates.

Neither is a blocked requirement. The mechanism is implemented; the values are data an
operator loads, and loading them opens the behaviour with no code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ProductionStartupBlockedError(RuntimeError):
    """Production refused to start because a required gate is unset."""


@dataclass(frozen=True, slots=True)
class OrderingSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "ordering"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    merchant_base_url: str | None = None
    merchant_service_credential: str | None = None
    serviceable_governorates: tuple[str, ...] = field(default_factory=tuple)
    require_prohibited_acknowledgement: bool = True
    nats_url: str | None = None
    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    @property
    def merchant_access_enabled(self) -> bool:
        return bool(self.merchant_base_url and self.merchant_service_credential)

    @property
    def serviceability_configured(self) -> bool:
        return bool(self.serviceable_governorates)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("ORDERING_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("ORDERING_IDENTITY_BASE_URL")
            missing.append("ORDERING_IDENTITY_SERVICE_CREDENTIAL")
        if not self.merchant_access_enabled:
            # Without Merchant, Ordering cannot tell a warehouse keeper from an owner, and
            # would have to either refuse every merchant or trust the token's own claim.
            missing.append("ORDERING_MERCHANT_BASE_URL")
            missing.append("ORDERING_MERCHANT_SERVICE_CREDENTIAL")
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


def _parse_list(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple, frozenset, set)):
        items = [str(item).strip().upper() for item in raw]
    else:
        items = [part.strip().upper() for part in str(raw).split(",")]
    return tuple(item for item in items if item)


def _parse_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_settings(**overrides: object) -> OrderingSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("ORDERING_ENVIRONMENT", "local")))
    )
    return OrderingSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "ORDERING_SERVICE_NAME", "ordering")
        ),
        database_url=_resolve(overrides, "database_url", "ORDERING_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "ORDERING_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "ORDERING_IDENTITY_SERVICE_CREDENTIAL",
        ),
        merchant_base_url=_resolve(
            overrides, "merchant_base_url", "ORDERING_MERCHANT_BASE_URL"
        ),
        merchant_service_credential=_resolve(
            overrides,
            "merchant_service_credential",
            "ORDERING_MERCHANT_SERVICE_CREDENTIAL",
        ),
        serviceable_governorates=_parse_list(
            _resolve(
                overrides,
                "serviceable_governorates",
                "ORDERING_SERVICEABLE_GOVERNORATES",
            )
        ),
        require_prohibited_acknowledgement=_parse_bool(
            _resolve(
                overrides,
                "require_prohibited_acknowledgement",
                "ORDERING_REQUIRE_PROHIBITED_ACKNOWLEDGEMENT",
            ),
            default=True,
        ),
        nats_url=_resolve(overrides, "nats_url", "ORDERING_NATS_URL"),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "ORDERING_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
