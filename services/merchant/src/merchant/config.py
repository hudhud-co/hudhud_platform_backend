"""Merchant settings.

The one setting that matters most here is ``application_required_attributes``. v6.3 p.10
records the merchant-application data set as an unresolved open item, so this service
ships with **no default field list**: leaving it unset means applications can be drafted
but not submitted. That is the fail-closed representation of MER-02 — the alternative,
inventing a plausible field list, would put a fabricated KYC policy into production.
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
class MerchantSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "merchant"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    #: MER-02. Empty means "not yet decided by the business", never "nothing is required".
    application_required_attributes: tuple[str, ...] = field(default_factory=tuple)
    nats_url: str | None = None
    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    @property
    def application_submission_enabled(self) -> bool:
        """False until the business decides what a merchant application must contain."""
        return bool(self.application_required_attributes)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("MERCHANT_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("MERCHANT_IDENTITY_BASE_URL")
            missing.append("MERCHANT_IDENTITY_SERVICE_CREDENTIAL")
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


def _parse_attributes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw]
    else:
        items = [part.strip() for part in str(raw).split(",")]
    return tuple(item for item in items if item)


def load_settings(**overrides: object) -> MerchantSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("MERCHANT_ENVIRONMENT", "local")))
    )
    return MerchantSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "MERCHANT_SERVICE_NAME", "merchant")
        ),
        database_url=_resolve(overrides, "database_url", "MERCHANT_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "MERCHANT_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "MERCHANT_IDENTITY_SERVICE_CREDENTIAL",
        ),
        application_required_attributes=_parse_attributes(
            _resolve(
                overrides,
                "application_required_attributes",
                "MERCHANT_APPLICATION_REQUIRED_ATTRIBUTES",
            )
        ),
        nats_url=_resolve(overrides, "nats_url", "MERCHANT_NATS_URL"),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "MERCHANT_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
