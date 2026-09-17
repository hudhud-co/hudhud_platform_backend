"""Identity settings. Secret values come from the environment only."""

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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True, slots=True)
class IdentitySettings:
    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "identity"
    database_url: str | None = None
    #: Keys every stored hash. Without it the service cannot hash a code or a token, so
    #: authentication is refused rather than run with a default key.
    signing_key: str | None = None
    otp_code_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_codes_per_window: int = 5
    otp_rate_limit_window_seconds: int = 900
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    #: Per-service shared secrets that may call token introspection.
    service_credentials: dict[str, str] = field(default_factory=dict)
    #: "console" prints codes to stderr for local development. Refused outside
    #: local/test so a retained log can never become an authentication bypass.
    otp_delivery_channel: str = "none"
    #: Phone number that receives the Operations role when its principal is first
    #: created. Without it a fresh deployment has no one who can grant any role.
    bootstrap_operations_phone: str | None = None

    @property
    def authentication_enabled(self) -> bool:
        return bool(self.signing_key)

    def assert_production_gates(self) -> None:
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if not self.database_url:
            missing.append("IDENTITY_DATABASE_URL")
        if not self.signing_key:
            missing.append("IDENTITY_SIGNING_KEY")
        if not self.service_credentials:
            missing.append("IDENTITY_SERVICE_CREDENTIALS")
        if missing:
            raise ProductionStartupBlockedError(
                "production startup blocked — unset gates: " + ", ".join(sorted(missing))
            )


def _parse_service_credentials(raw: str | None) -> dict[str, str]:
    """Parse ``name:secret`` pairs. Values are never logged."""
    if not raw or not raw.strip():
        return {}
    parsed: dict[str, str] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        name, secret = item.split(":", 1)
        if name.strip() and secret.strip():
            parsed[name.strip()] = secret.strip()
    return parsed


def load_settings(**overrides: object) -> IdentitySettings:
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("IDENTITY_ENVIRONMENT", "local")))
    )
    database_url = overrides.get("database_url")
    if database_url is None:
        database_url = os.environ.get("IDENTITY_DATABASE_URL") or os.environ.get(
            "DATABASE_URL"
        )
    signing_key = overrides.get("signing_key")
    if signing_key is None:
        signing_key = os.environ.get("IDENTITY_SIGNING_KEY")
    credentials = overrides.get("service_credentials")
    if credentials is None:
        credentials = _parse_service_credentials(
            os.environ.get("IDENTITY_SERVICE_CREDENTIALS")
        )
    channel = str(
        overrides.get(
            "otp_delivery_channel",
            os.environ.get("IDENTITY_OTP_DELIVERY_CHANNEL", "none"),
        )
    ).strip().lower()
    if channel == "console" and environment in (
        RuntimeEnvironment.STAGING,
        RuntimeEnvironment.PRODUCTION,
    ):
        msg = "console OTP delivery is not permitted outside local and test environments"
        raise ProductionStartupBlockedError(msg)

    bootstrap_phone = overrides.get("bootstrap_operations_phone")
    if bootstrap_phone is None:
        bootstrap_phone = os.environ.get("IDENTITY_BOOTSTRAP_OPERATIONS_PHONE")

    return IdentitySettings(
        environment=environment,
        service_name=str(
            overrides.get("service_name", os.environ.get("IDENTITY_SERVICE_NAME", "identity"))
        ),
        database_url=database_url,  # type: ignore[arg-type]
        signing_key=signing_key,  # type: ignore[arg-type]
        otp_code_ttl_seconds=int(
            overrides.get("otp_code_ttl_seconds", _env_int("IDENTITY_OTP_TTL_SECONDS", 300))
        ),
        otp_max_attempts=int(
            overrides.get("otp_max_attempts", _env_int("IDENTITY_OTP_MAX_ATTEMPTS", 5))
        ),
        otp_max_codes_per_window=int(
            overrides.get(
                "otp_max_codes_per_window", _env_int("IDENTITY_OTP_MAX_CODES", 5)
            )
        ),
        otp_rate_limit_window_seconds=int(
            overrides.get(
                "otp_rate_limit_window_seconds",
                _env_int("IDENTITY_OTP_WINDOW_SECONDS", 900),
            )
        ),
        session_ttl_seconds=int(
            overrides.get(
                "session_ttl_seconds",
                _env_int("IDENTITY_SESSION_TTL_SECONDS", 60 * 60 * 24 * 30),
            )
        ),
        service_credentials=dict(credentials),  # type: ignore[arg-type]
        otp_delivery_channel=channel,
        bootstrap_operations_phone=bootstrap_phone,  # type: ignore[arg-type]
    )
