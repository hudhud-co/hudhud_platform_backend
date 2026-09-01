"""Service configuration — production startup blocked until explicit gates pass."""

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
    """Raised when production configuration gates are not satisfied."""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


class PersistenceBackend(StrEnum):
    POSTGRES = "postgres"
    MEMORY = "memory"


_DEFAULT_JWT_ALGORITHMS = ("RS256", "ES256")


@dataclass(frozen=True, slots=True)
class JwtSettings:
    """JWT/JWKS query authorization settings."""

    issuer: str | None = None
    audience: str | None = None
    jwks_url: str | None = None
    allowed_algorithms: tuple[str, ...] = _DEFAULT_JWT_ALGORITHMS
    jwks_timeout_seconds: float = 5.0
    jwks_cache_ttl_seconds: int = 300

    def is_complete(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url and self.allowed_algorithms)

    def requires_https_jwks(self, environment: RuntimeEnvironment) -> bool:
        return environment in {RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION}


@dataclass(frozen=True, slots=True)
class TrackingSettings:
    """Tracking service settings. Secret values must come from environment only."""

    environment: RuntimeEnvironment = RuntimeEnvironment.LOCAL
    service_name: str = "tracking"
    database_url: str = "postgresql+psycopg://localhost/tracking"
    persistence_backend: PersistenceBackend = PersistenceBackend.POSTGRES
    consumer_name: str = "tracking_bridge_timeline_v1"
    handler_version: str = "0.1.0"
    processing_owner: str = "tracking-worker"
    inbox_lease_seconds: int = 30
    inbox_max_attempts: int = 5
    nats_enabled: bool = False
    nats_url: str | None = None
    nats_tls_enabled: bool = False
    nats_tls_ca_file: str | None = None
    nats_user: str | None = None
    nats_password: str | None = None
    nats_token: str | None = None
    nats_creds_file: str | None = None
    allow_no_auth_local: bool = True
    pull_batch_size: int = 10
    pull_fetch_timeout_seconds: float = 5.0
    handler_concurrency: int = 4
    defer_delay_seconds: float = 5.0
    shutdown_timeout_seconds: float = 30.0
    adr_0010_credentials_configured: bool = False
    jwt: JwtSettings = JwtSettings()

    def assert_production_gates(self) -> None:
        """Block production startup until ADR-0010 credentials are configured."""
        if self.environment is not RuntimeEnvironment.PRODUCTION:
            return
        if not self.adr_0010_credentials_configured:
            msg = "Production startup blocked — unset gates: adr_0010_credentials_configured"
            raise ProductionStartupBlockedError(msg)
        if self.persistence_backend is PersistenceBackend.MEMORY:
            msg = "Production startup blocked — in-memory persistence is forbidden"
            raise ProductionStartupBlockedError(msg)

    def assert_query_auth_gates(self) -> None:
        """Block staging/production startup until JWT/JWKS query auth is fully configured."""
        if self.environment not in {RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION}:
            return
        missing: list[str] = []
        if not self.jwt.issuer:
            missing.append("jwt_issuer")
        if not self.jwt.audience:
            missing.append("jwt_audience")
        if not self.jwt.jwks_url:
            missing.append("jwt_jwks_url")
        if not self.jwt.allowed_algorithms:
            missing.append("jwt_allowed_algorithms")
        if missing:
            names = ", ".join(missing)
            msg = f"Query auth startup blocked — unset gates: {names}"
            raise ProductionStartupBlockedError(msg)
        if self.jwt.requires_https_jwks(self.environment) and not self.jwt.jwks_url.startswith(
            "https://"
        ):
            msg = "Query auth startup blocked — jwt_jwks_url must use https in staging/production"
            raise ProductionStartupBlockedError(msg)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _optional_str(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw


def _env_algorithms(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    values = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return values or default


def _load_jwt_settings(overrides: dict[str, object]) -> JwtSettings:
    return JwtSettings(
        issuer=overrides.get("jwt_issuer", _optional_str("TRACKING_JWT_ISSUER")),
        audience=overrides.get("jwt_audience", _optional_str("TRACKING_JWT_AUDIENCE")),
        jwks_url=overrides.get("jwt_jwks_url", _optional_str("TRACKING_JWT_JWKS_URL")),
        allowed_algorithms=tuple(
            overrides.get(
                "jwt_allowed_algorithms",
                _env_algorithms("TRACKING_JWT_ALLOWED_ALGORITHMS", _DEFAULT_JWT_ALGORITHMS),
            )
        ),
        jwks_timeout_seconds=float(
            overrides.get(
                "jwt_jwks_timeout_seconds",
                _env_float("TRACKING_JWT_JWKS_TIMEOUT_SECONDS", 5.0),
            )
        ),
        jwks_cache_ttl_seconds=int(
            overrides.get(
                "jwt_jwks_cache_ttl_seconds",
                _env_int("TRACKING_JWT_JWKS_CACHE_TTL_SECONDS", 300),
            )
        ),
    )


def load_settings(**overrides: object) -> TrackingSettings:
    """Load settings from environment with optional test overrides."""
    environment = RuntimeEnvironment(
        str(overrides.get("environment", os.environ.get("TRACKING_ENVIRONMENT", "local")))
    )
    nats_url = overrides.get("nats_url", os.environ.get("TRACKING_NATS_URL") or None)
    if isinstance(nats_url, str) and nats_url.strip() == "":
        nats_url = None
    persistence_backend = PersistenceBackend(
        str(
            overrides.get(
                "persistence_backend",
                os.environ.get("TRACKING_PERSISTENCE_BACKEND", PersistenceBackend.POSTGRES.value),
            )
        )
    )
    values: dict[str, object] = {
        "environment": environment,
        "service_name": str(
            overrides.get("service_name", os.environ.get("TRACKING_SERVICE_NAME", "tracking"))
        ),
        "database_url": str(
            overrides.get(
                "database_url",
                os.environ.get("TRACKING_DATABASE_URL", "postgresql+psycopg://localhost/audit"),
            )
        ),
        "persistence_backend": persistence_backend,
        "consumer_name": str(
            overrides.get(
                "consumer_name",
                os.environ.get("TRACKING_CONSUMER_NAME", "tracking_bridge_timeline_v1"),
            )
        ),
        "handler_version": str(
            overrides.get("handler_version", os.environ.get("TRACKING_HANDLER_VERSION", "0.1.0"))
        ),
        "processing_owner": str(
            overrides.get(
                "processing_owner",
                os.environ.get("TRACKING_PROCESSING_OWNER", "tracking-worker"),
            )
        ),
        "inbox_lease_seconds": int(
            overrides.get(
                "inbox_lease_seconds",
                _env_int("TRACKING_INBOX_LEASE_SECONDS", 30),
            )
        ),
        "inbox_max_attempts": int(
            overrides.get(
                "inbox_max_attempts",
                _env_int("TRACKING_INBOX_MAX_ATTEMPTS", 5),
            )
        ),
        "nats_enabled": bool(
            overrides.get("nats_enabled", _env_bool("TRACKING_NATS_ENABLED", default=False))
        ),
        "nats_url": nats_url,
        "nats_tls_enabled": bool(
            overrides.get("nats_tls_enabled", _env_bool("TRACKING_NATS_TLS_ENABLED", default=False))
        ),
        "nats_tls_ca_file": overrides.get(
            "nats_tls_ca_file",
            _optional_str("TRACKING_NATS_TLS_CA_FILE"),
        ),
        "nats_user": overrides.get("nats_user", _optional_str("TRACKING_NATS_USER")),
        "nats_password": overrides.get("nats_password", _optional_str("TRACKING_NATS_PASSWORD")),
        "nats_token": overrides.get("nats_token", _optional_str("TRACKING_NATS_TOKEN")),
        "nats_creds_file": overrides.get(
            "nats_creds_file",
            _optional_str("TRACKING_NATS_CREDS_FILE"),
        ),
        "allow_no_auth_local": bool(
            overrides.get(
                "allow_no_auth_local",
                _env_bool("TRACKING_ALLOW_NO_AUTH_LOCAL", default=True),
            )
        ),
        "pull_batch_size": int(
            overrides.get("pull_batch_size", _env_int("TRACKING_PULL_BATCH_SIZE", 10))
        ),
        "pull_fetch_timeout_seconds": float(
            overrides.get(
                "pull_fetch_timeout_seconds",
                _env_float("TRACKING_PULL_FETCH_TIMEOUT_SECONDS", 5.0),
            )
        ),
        "handler_concurrency": int(
            overrides.get("handler_concurrency", _env_int("TRACKING_HANDLER_CONCURRENCY", 4))
        ),
        "defer_delay_seconds": float(
            overrides.get("defer_delay_seconds", _env_float("TRACKING_DEFER_DELAY_SECONDS", 5.0))
        ),
        "shutdown_timeout_seconds": float(
            overrides.get(
                "shutdown_timeout_seconds",
                _env_float("TRACKING_SHUTDOWN_TIMEOUT_SECONDS", 30.0),
            )
        ),
        "adr_0010_credentials_configured": bool(
            overrides.get(
                "adr_0010_credentials_configured",
                _env_bool("TRACKING_ADR_0010_CREDENTIALS_CONFIGURED", default=False),
            )
        ),
        "jwt": overrides.get("jwt", _load_jwt_settings(overrides)),
    }
    return TrackingSettings(**values)  # type: ignore[arg-type]
