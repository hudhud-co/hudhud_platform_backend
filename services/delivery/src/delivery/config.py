"""Delivery settings.

Two of these are unlike the rest. ``delivery_code_length`` and
``id_photo_retention_decided`` stand in for business decisions that the authoritative
sources contradict each other on, so neither has a usable default and both fail closed:
see ``domain/delivery_code.py`` and ``domain/id_evidence.py`` for the two readings, and
the audit pack for the decisions they are waiting on.
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
class DeliverySettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "delivery"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    workforce_base_url: str | None = None
    workforce_service_credential: str | None = None
    nats_url: str | None = None

    #: DRV-L05 — SOURCE_CONFLICT. Customer App v3 says four digits, Driver App v8 shows
    #: six. There is no default: a guess here would be a silent product decision, and a
    #: wrong one would reject every real receiver at the door.
    delivery_code_length: int | None = None
    #: The HMAC key the delivery-code digest is derived with. Without it no code can be
    #: set or checked, which is the correct behaviour rather than an unkeyed hash.
    delivery_code_hmac_key: str | None = None

    #: DRV-L07 — SOURCE_CONFLICT. Until the retention period is decided, no ID
    #: photograph is retained; there is no column to retain one in either.
    id_photo_retention_decided: bool = False

    #: v6.3 p.26 wait is a constant, not configuration — both sources say ten minutes.
    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    @property
    def workforce_eligibility_enabled(self) -> bool:
        return bool(self.workforce_base_url and self.workforce_service_credential)

    @property
    def delivery_code_decided(self) -> bool:
        return self.delivery_code_length is not None

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("DELIVERY_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("DELIVERY_IDENTITY_BASE_URL")
            missing.append("DELIVERY_IDENTITY_SERVICE_CREDENTIAL")
        if not self.workforce_eligibility_enabled:
            missing.append("DELIVERY_WORKFORCE_BASE_URL")
            missing.append("DELIVERY_WORKFORCE_SERVICE_CREDENTIAL")
        if not self.delivery_code_hmac_key:
            missing.append("DELIVERY_CODE_HMAC_KEY")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )
        # DELIVERY_CODE_LENGTH is deliberately *not* in that list. Production may run
        # without it: everything except code verification works, and the audit pack
        # would rather have a service that refuses one operation than one that starts by
        # inventing the answer.


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


def _parse_code_length(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    length = int(raw)  # type: ignore[arg-type]
    if length <= 0:
        msg = "DELIVERY_CODE_LENGTH must be a positive number of digits"
        raise ValueError(msg)
    return length


def load_settings(**overrides: object) -> DeliverySettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("DELIVERY_ENVIRONMENT", "local")))
    )
    return DeliverySettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "DELIVERY_SERVICE_NAME", "delivery")
        ),
        database_url=_resolve(overrides, "database_url", "DELIVERY_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "DELIVERY_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "DELIVERY_IDENTITY_SERVICE_CREDENTIAL",
        ),
        workforce_base_url=_resolve(
            overrides, "workforce_base_url", "DELIVERY_WORKFORCE_BASE_URL"
        ),
        workforce_service_credential=_resolve(
            overrides,
            "workforce_service_credential",
            "DELIVERY_WORKFORCE_SERVICE_CREDENTIAL",
        ),
        nats_url=_resolve(overrides, "nats_url", "DELIVERY_NATS_URL"),
        delivery_code_length=_parse_code_length(
            _resolve(overrides, "delivery_code_length", "DELIVERY_CODE_LENGTH")
        ),
        delivery_code_hmac_key=_resolve(
            overrides, "delivery_code_hmac_key", "DELIVERY_CODE_HMAC_KEY"
        ),
        id_photo_retention_decided=_parse_bool(
            _resolve(
                overrides,
                "id_photo_retention_decided",
                "DELIVERY_ID_PHOTO_RETENTION_DECIDED",
            ),
            default=False,
        ),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "DELIVERY_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
