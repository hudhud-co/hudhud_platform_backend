"""Finance settings.

Three of these carry business decisions rather than deployment details.

* ``default_cash_limit_minor_units`` — DRV-A06. v6.3 says a per-driver cash limit exists;
  it never says what it is, so there is a configured platform default and no number in
  code. A driver's own limit overrides it.
* ``return_trip_fee_minor_units`` — PAY-08's tariff. With none configured, a refusal
  charge is **refused** rather than guessed at.
* ``return_fee_waiver_permitted`` — PAY-08's Open Item. Off, with no safe default on:
  whether HUDHUD may waive the return-trip fee is what the accountant has to decide.

There is no setting that would let a payout be paid out. That is PAY-07, and unlike a
tariff it is not a number — it is a procedure per method that nobody has written down.
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
class FinanceSettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "finance"
    database_url: str | None = None
    identity_base_url: str | None = None
    identity_service_credential: str | None = None
    nats_url: str | None = None

    #: DRV-A06 — the platform default when a driver has no limit of their own.
    default_cash_limit_minor_units: int | None = None
    #: PAY-08 — the return-trip tariff. None means a refusal charge is refused.
    return_trip_fee_minor_units: int | None = None
    #: PAY-08 Open Item — off until an accountant says HUDHUD may waive.
    return_fee_waiver_permitted: bool = False

    outbox_max_attempts: int = 5

    @property
    def identity_authorization_enabled(self) -> bool:
        return bool(self.identity_base_url and self.identity_service_credential)

    @property
    def cash_limit_configured(self) -> bool:
        return self.default_cash_limit_minor_units is not None

    @property
    def return_trip_fee_configured(self) -> bool:
        return self.return_trip_fee_minor_units is not None

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("FINANCE_DATABASE_URL")
        if not self.identity_authorization_enabled:
            missing.append("FINANCE_IDENTITY_BASE_URL")
            missing.append("FINANCE_IDENTITY_SERVICE_CREDENTIAL")
        if not self.cash_limit_configured:
            # Without a default limit there is no cap on how much cash a driver may
            # carry, which is the opposite of what DRV-A06 asks for. Unlike the two
            # Open Items, this one has to be answered before the service runs.
            missing.append("FINANCE_DEFAULT_CASH_LIMIT")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )
        # FINANCE_RETURN_TRIP_FEE is deliberately absent from that list. Without it a
        # refusal charge is refused, which is correct behaviour for an unconfigured
        # tariff; a service that will not boot would be worse.


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


def _parse_minor_units(raw: object, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    amount = int(raw)  # type: ignore[arg-type]
    if amount < 0:
        msg = f"{name} cannot be negative"
        raise ValueError(msg)
    return amount


def load_settings(**overrides: object) -> FinanceSettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("FINANCE_ENVIRONMENT", "local")))
    )
    return FinanceSettings(
        environment=environment,
        service_name=str(
            _resolve(overrides, "service_name", "FINANCE_SERVICE_NAME", "finance")
        ),
        database_url=_resolve(overrides, "database_url", "FINANCE_DATABASE_URL"),
        identity_base_url=_resolve(
            overrides, "identity_base_url", "FINANCE_IDENTITY_BASE_URL"
        ),
        identity_service_credential=_resolve(
            overrides,
            "identity_service_credential",
            "FINANCE_IDENTITY_SERVICE_CREDENTIAL",
        ),
        nats_url=_resolve(overrides, "nats_url", "FINANCE_NATS_URL"),
        default_cash_limit_minor_units=_parse_minor_units(
            _resolve(
                overrides, "default_cash_limit_minor_units", "FINANCE_DEFAULT_CASH_LIMIT"
            ),
            "FINANCE_DEFAULT_CASH_LIMIT",
        ),
        return_trip_fee_minor_units=_parse_minor_units(
            _resolve(
                overrides, "return_trip_fee_minor_units", "FINANCE_RETURN_TRIP_FEE"
            ),
            "FINANCE_RETURN_TRIP_FEE",
        ),
        return_fee_waiver_permitted=_parse_bool(
            _resolve(
                overrides,
                "return_fee_waiver_permitted",
                "FINANCE_RETURN_FEE_WAIVER_PERMITTED",
            ),
            default=False,
        ),
        outbox_max_attempts=int(
            _resolve(overrides, "outbox_max_attempts", "FINANCE_OUTBOX_MAX_ATTEMPTS", 5)  # type: ignore[arg-type]
        ),
    )
