"""Hub settings.

``drop_off_hold_days`` carries CUS-06 — "Unclaimed orders are cancelled after 3 days" — as
configuration rather than a constant, because how long a hub holds a walk-in parcel is an
operational choice the business may revise without a release.
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
class HubSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "hub"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    #: CUS-06 — how long an unclaimed drop-off waits before it is cancelled.
    drop_off_hold_days: int = 3
    nats_url: str | None = None
    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("HUB_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("HUB_IDENTITY_BASE_URL")
            missing.append("HUB_IDENTITY_SERVICE_CREDENTIAL")
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


def load_settings(**overrides: object) -> HubSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("HUB_ENVIRONMENT", "local")))
    )
    return HubSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "HUB_SERVICE_NAME", "hub")
        ),
        database_url=_resolve(overrides, "database_url", "HUB_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "HUB_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "HUB_IDENTITY_SERVICE_CREDENTIAL",
        ),
        drop_off_hold_days=int(
            _resolve(overrides, "drop_off_hold_days", "HUB_DROP_OFF_HOLD_DAYS", 3)  # type: ignore[arg-type]
        ),
        nats_url=_resolve(overrides, "nats_url", "HUB_NATS_URL"),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "HUB_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
