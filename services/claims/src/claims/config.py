"""Claims settings.

One of these is unlike the rest. ``high_value_threshold_minor_units`` stands in for
CLM-08, a v6.3 Open Item (Appendix A, p.44): the declared value above which a parcel gets
different handling and HUDHUD a different exposure. It has no default, and asking whether
a parcel is high value raises until it is set — see ``domain/high_value.py``. Everything
else in this service works without it.
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
class ClaimsSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "claims"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    nats_url: str | None = None

    #: CLM-08 — BLOCKED_BUSINESS_DECISION. No default: a guess here would be a silent
    #: product decision about which parcels get extra scrutiny.
    high_value_threshold_minor_units: int | None = None

    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    @property
    def high_value_threshold_decided(self) -> bool:
        return self.high_value_threshold_minor_units is not None

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("CLAIMS_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("CLAIMS_IDENTITY_BASE_URL")
            missing.append("CLAIMS_IDENTITY_SERVICE_CREDENTIAL")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )
        # CLAIMS_HIGH_VALUE_THRESHOLD is deliberately *not* in that list. Production may
        # run without it: filing, review, approval, rejection and the support thread
        # never ask, and a service that refuses one question is better than one that
        # starts by inventing the answer.


_MISSING = object()


def _resolve(overrides: dict, key: str, env_name: str, default=None):
    value = overrides.get(key, _MISSING)
    if value is not _MISSING:
        return value
    return os.environ.get(env_name, default)


def _parse_threshold(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    threshold = int(raw)  # type: ignore[arg-type]
    if threshold <= 0:
        msg = "CLAIMS_HIGH_VALUE_THRESHOLD must be a positive amount of minor units"
        raise ValueError(msg)
    return threshold


def load_settings(**overrides: object) -> ClaimsSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("CLAIMS_ENVIRONMENT", "local")))
    )
    return ClaimsSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "CLAIMS_SERVICE_NAME", "claims")
        ),
        database_url=_resolve(overrides, "database_url", "CLAIMS_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "CLAIMS_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "CLAIMS_IDENTITY_SERVICE_CREDENTIAL",
        ),
        nats_url=_resolve(overrides, "nats_url", "CLAIMS_NATS_URL"),
        high_value_threshold_minor_units=_parse_threshold(
            _resolve(
                overrides,
                "high_value_threshold_minor_units",
                "CLAIMS_HIGH_VALUE_THRESHOLD",
            )
        ),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "CLAIMS_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
