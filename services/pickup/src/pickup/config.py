"""Service configuration — production startup blocked until explicit gates pass."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum

DEFAULT_OUTBOX_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5, 30, 120, 600, 1800)
MAX_OUTBOX_RETRY_BACKOFF_ENTRIES = 10
MIN_OUTBOX_RETRY_BACKOFF_SECONDS = 1
MAX_OUTBOX_RETRY_BACKOFF_SECONDS = 86_400


class RuntimeEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class PersistenceBackend(StrEnum):
    POSTGRES = "postgres"
    MEMORY = "memory"


class ProductionStartupBlockedError(RuntimeError):
    """Raised when production configuration gates are not satisfied."""


def _optional_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _parse_backoff(raw: str | None) -> list[int]:
    if raw is None or raw.strip() == "":
        return list(DEFAULT_OUTBOX_RETRY_BACKOFF_SECONDS)
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    _validate_backoff(values)
    return values


def _validate_backoff(values: list[int]) -> list[int]:
    if not values:
        msg = "outbox_retry_backoff_seconds must contain at least one entry"
        raise ValueError(msg)
    if len(values) > MAX_OUTBOX_RETRY_BACKOFF_ENTRIES:
        msg = (
            f"outbox_retry_backoff_seconds exceeds maximum of "
            f"{MAX_OUTBOX_RETRY_BACKOFF_ENTRIES} entries"
        )
        raise ValueError(msg)
    for value in values:
        if value < MIN_OUTBOX_RETRY_BACKOFF_SECONDS:
            msg = (
                f"outbox retry backoff must be >= {MIN_OUTBOX_RETRY_BACKOFF_SECONDS} seconds"
            )
            raise ValueError(msg)
        if value > MAX_OUTBOX_RETRY_BACKOFF_SECONDS:
            msg = (
                f"outbox retry backoff must be <= {MAX_OUTBOX_RETRY_BACKOFF_SECONDS} seconds"
            )
            raise ValueError(msg)
    return values


@dataclass(frozen=True, slots=True)
class PickupSettings:
    """Pickup service settings. Secret values must come from environment only."""

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "pickup"
    database_url: str | None = None
    persistence_backend: PersistenceBackend = PersistenceBackend.POSTGRES
    production_ready: bool = False

    adr_0010_credentials_configured: bool = False

    relay_enabled: bool = False
    relay_owner_id: str = "pickup-relay"
    relay_batch_size: int = 50
    relay_poll_interval_seconds: float = 1.0
    relay_publish_timeout_seconds: float = 5.0
    outbox_lease_seconds: int = 30
    outbox_retry_backoff_seconds: list[int] = field(
        default_factory=lambda: list(DEFAULT_OUTBOX_RETRY_BACKOFF_SECONDS)
    )
    relay_transport_max_msg_bytes: int = 256 * 1024

    nats_url: str | None = None
    nats_dev_no_auth: bool = False
    nats_user: str | None = None
    nats_password: str | None = None
    nats_token: str | None = None
    nats_creds_file: str | None = None
    nats_tls_enabled: bool = False
    nats_tls_ca_file: str | None = None
    nats_connect_timeout_seconds: float = 5.0

    def assert_production_gates(self) -> None:
        """Block production startup when persistence or NATS gates are unsafe."""
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        missing: list[str] = []
        if self.persistence_backend is PersistenceBackend.MEMORY:
            msg = "Production startup blocked — in-memory persistence is forbidden"
            raise ProductionStartupBlockedError(msg)
        if not self.database_url:
            missing.append("DATABASE_URL")
        if self.relay_enabled and not self.adr_0010_credentials_configured:
            missing.append("adr_0010_credentials_configured")
        if self.production_ready:
            missing.append("production_ready_must_remain_false")
        if missing:
            joined = ", ".join(missing)
            msg = f"Production startup blocked — unset gates: {joined}"
            raise ProductionStartupBlockedError(msg)

    def relay_configuration_valid(self) -> bool:
        """Return True when relay NATS settings are internally consistent."""
        if not self.relay_enabled:
            return True
        if not self.nats_url:
            return False
        has_credentials = bool(
            self.nats_token
            or self.nats_creds_file
            or (self.nats_user and self.nats_password)
        )
        if self.nats_dev_no_auth:
            return self.environment is not RuntimeEnvironment.PRODUCTION
        if self.environment is RuntimeEnvironment.PRODUCTION:
            return (
                self.adr_0010_credentials_configured
                and self.nats_tls_enabled
                and has_credentials
            )
        return has_credentials


def load_settings(**overrides: object) -> PickupSettings:
    """Load settings from environment with optional test overrides."""
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("PICKUP_ENVIRONMENT", "local")))
    )

    if "database_url" in overrides:
        raw_url = overrides["database_url"]
        database_url = None if raw_url is None or str(raw_url).strip() == "" else str(raw_url)
    else:
        database_url = _optional_str("DATABASE_URL") or _optional_str("PICKUP_DATABASE_URL")

    persistence_backend = PersistenceBackend(
        str(
            overrides.get(
                "persistence_backend",
                os.environ.get("PICKUP_PERSISTENCE_BACKEND", PersistenceBackend.POSTGRES.value),
            )
        )
    )

    if "outbox_retry_backoff_seconds" in overrides:
        backoff = list(overrides["outbox_retry_backoff_seconds"])  # type: ignore[arg-type]
        _validate_backoff(backoff)
    else:
        backoff = _parse_backoff(os.environ.get("PICKUP_OUTBOX_RETRY_BACKOFF_SECONDS"))

    def _override_or(name: str, env_name: str, default: object) -> object:
        if name in overrides:
            return overrides[name]
        if isinstance(default, bool):
            return _env_bool(env_name, default)
        if isinstance(default, float):
            return _env_float(env_name, default)
        if isinstance(default, int):
            return _env_int(env_name, default)
        env_val = _optional_str(env_name)
        return default if env_val is None else env_val

    return PickupSettings(
        environment=environment,
        service_name=str(
            overrides.get("service_name", os.environ.get("PICKUP_SERVICE_NAME", "pickup"))
        ),
        database_url=database_url,
        persistence_backend=persistence_backend,
        production_ready=bool(
            _override_or("production_ready", "PICKUP_PRODUCTION_READY", False)
        ),
        adr_0010_credentials_configured=bool(
            _override_or(
                "adr_0010_credentials_configured",
                "PICKUP_ADR_0010_CREDENTIALS_CONFIGURED",
                False,
            )
        ),
        relay_enabled=bool(_override_or("relay_enabled", "PICKUP_RELAY_ENABLED", False)),
        relay_owner_id=str(
            _override_or("relay_owner_id", "PICKUP_RELAY_OWNER_ID", "pickup-relay")
        ),
        relay_batch_size=int(
            _override_or("relay_batch_size", "PICKUP_RELAY_BATCH_SIZE", 50)  # type: ignore[arg-type]
        ),
        relay_poll_interval_seconds=float(
            _override_or(
                "relay_poll_interval_seconds",
                "PICKUP_RELAY_POLL_INTERVAL_SECONDS",
                1.0,
            )  # type: ignore[arg-type]
        ),
        relay_publish_timeout_seconds=float(
            _override_or(
                "relay_publish_timeout_seconds",
                "PICKUP_RELAY_PUBLISH_TIMEOUT_SECONDS",
                5.0,
            )  # type: ignore[arg-type]
        ),
        outbox_lease_seconds=int(
            _override_or("outbox_lease_seconds", "PICKUP_OUTBOX_LEASE_SECONDS", 30)  # type: ignore[arg-type]
        ),
        outbox_retry_backoff_seconds=backoff,
        relay_transport_max_msg_bytes=int(
            _override_or(
                "relay_transport_max_msg_bytes",
                "PICKUP_RELAY_TRANSPORT_MAX_MSG_BYTES",
                256 * 1024,
            )  # type: ignore[arg-type]
        ),
        nats_url=(
            None
            if "nats_url" in overrides and overrides["nats_url"] is None
            else str(
                _override_or("nats_url", "PICKUP_NATS_URL", _optional_str("NATS_URL") or "")
            )
            or None
        ),
        nats_dev_no_auth=bool(
            _override_or("nats_dev_no_auth", "PICKUP_NATS_DEV_NO_AUTH", False)
        ),
        nats_user=(
            None
            if "nats_user" in overrides and overrides["nats_user"] is None
            else str(_override_or("nats_user", "PICKUP_NATS_USER", "") or "") or None
        ),
        nats_password=(
            None
            if "nats_password" in overrides and overrides["nats_password"] is None
            else str(_override_or("nats_password", "PICKUP_NATS_PASSWORD", "") or "") or None
        ),
        nats_token=(
            None
            if "nats_token" in overrides and overrides["nats_token"] is None
            else str(_override_or("nats_token", "PICKUP_NATS_TOKEN", "") or "") or None
        ),
        nats_creds_file=(
            None
            if "nats_creds_file" in overrides and overrides["nats_creds_file"] is None
            else str(_override_or("nats_creds_file", "PICKUP_NATS_CREDS_FILE", "") or "")
            or None
        ),
        nats_tls_enabled=bool(
            _override_or("nats_tls_enabled", "PICKUP_NATS_TLS_ENABLED", False)
        ),
        nats_tls_ca_file=(
            None
            if "nats_tls_ca_file" in overrides and overrides["nats_tls_ca_file"] is None
            else str(_override_or("nats_tls_ca_file", "PICKUP_NATS_TLS_CA_FILE", "") or "")
            or None
        ),
        nats_connect_timeout_seconds=float(
            _override_or(
                "nats_connect_timeout_seconds",
                "PICKUP_NATS_CONNECT_TIMEOUT_SECONDS",
                5.0,
            )  # type: ignore[arg-type]
        ),
    )
