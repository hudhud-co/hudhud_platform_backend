"""Workforce settings.

The lateness numbers are configuration and not constants. v6.3 fixes neither a grace
period nor a blocking threshold, and the Driver App shows only one worked example, so a
number in code would be an invented penalty applied to real drivers.
"""

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
class WorkforceSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "workforce"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    #: DRV-A02 — minutes of lateness tolerated before it is counted at all.
    lateness_grace_minutes: int = 0
    #: DRV-A02 — minutes of lateness at which assignment is paused.
    lateness_block_after_minutes: int = 1
    #: DRV-A01 — whether assigned work unlocks only once the shift is started.
    require_started_shift_for_assignment: bool = True
    nats_url: str | None = None

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("WORKFORCE_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("WORKFORCE_IDENTITY_BASE_URL")
            missing.append("WORKFORCE_IDENTITY_SERVICE_CREDENTIAL")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )


def _parse_bool(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_MISSING = object()


def _resolve(overrides: dict, key: str, env_name: str, default=None):
    value = overrides.get(key, _MISSING)
    if value is not _MISSING:
        return value
    return os.environ.get(env_name, default)


def load_settings(**overrides: object) -> WorkforceSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("WORKFORCE_ENVIRONMENT", "local")))
    )
    return WorkforceSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "WORKFORCE_SERVICE_NAME", "workforce")
        ),
        database_url=_resolve(overrides, "database_url", "WORKFORCE_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "WORKFORCE_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "WORKFORCE_IDENTITY_SERVICE_CREDENTIAL",
        ),
        lateness_grace_minutes=int(
            _resolve(
                overrides, "lateness_grace_minutes", "WORKFORCE_LATENESS_GRACE_MINUTES", 0
            )  # type: ignore[arg-type]
        ),
        lateness_block_after_minutes=int(
            _resolve(
                overrides,
                "lateness_block_after_minutes",
                "WORKFORCE_LATENESS_BLOCK_AFTER_MINUTES",
                1,
            )  # type: ignore[arg-type]
        ),
        require_started_shift_for_assignment=_parse_bool(
            _resolve(
                overrides,
                "require_started_shift_for_assignment",
                "WORKFORCE_REQUIRE_STARTED_SHIFT",
            ),
            default=True,
        ),
        nats_url=_resolve(overrides, "nats_url", "WORKFORCE_NATS_URL"),
    )
