"""NATS connection options and durable bind — no topology mutation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from tracking.config import RuntimeEnvironment, TrackingSettings
from tracking.domain.contract import A1_DURABLE_CONSUMER, A1_STREAM
from tracking.infrastructure.jetstream.binding import (
    ConsumerBindingMismatchError,
    verify_consumer_info,
)

logger = logging.getLogger("tracking.jetstream.connection")


class NatsAuthRequiredError(RuntimeError):
    """Raised when production-like environments require explicit NATS credentials."""


@dataclass(frozen=True, slots=True)
class NatsConnectionReport:
    connected: bool
    binding_verified: bool
    stream: str
    durable_name: str


class JetStreamPullPort(Protocol):
    async def pull_subscribe_bind(
        self,
        *,
        durable: str,
        stream: str,
    ) -> Any: ...

    async def consumer_info(self, stream: str, consumer: str) -> Any: ...


class NatsClientPort(Protocol):
    def jetstream(self) -> JetStreamPullPort: ...

    async def close(self) -> None: ...


def build_nats_connect_options(settings: TrackingSettings) -> dict[str, Any]:
    """Build nats.connect kwargs — credentials from environment only."""
    if not settings.nats_url:
        msg = "NATS URL is not configured"
        raise RuntimeError(msg)

    options: dict[str, Any] = {"servers": [settings.nats_url]}

    if settings.nats_tls_enabled:
        options["tls"] = True

    if settings.nats_user:
        options["user"] = settings.nats_user
    if settings.nats_password:
        options["password"] = settings.nats_password
    if settings.nats_token:
        options["token"] = settings.nats_token
    if settings.nats_creds_file:
        options["user_credentials"] = settings.nats_creds_file

    has_credentials = any(
        (
            settings.nats_user,
            settings.nats_password,
            settings.nats_token,
            settings.nats_creds_file,
            settings.adr_0010_credentials_configured,
        )
    )
    if settings.environment is RuntimeEnvironment.PRODUCTION and not has_credentials:
        msg = "Production NATS requires ADR-0010 credential gate"
        raise NatsAuthRequiredError(msg)

    if settings.environment is RuntimeEnvironment.PRODUCTION and not settings.nats_tls_enabled:
        msg = "Production NATS requires TLS"
        raise NatsAuthRequiredError(msg)

    if settings.environment in {RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST}:
        if settings.allow_no_auth_local or has_credentials:
            return options
        msg = "Local NATS without auth requires TRACKING_ALLOW_NO_AUTH_LOCAL"
        raise NatsAuthRequiredError(msg)

    if not has_credentials:
        msg = "NATS credentials are required outside explicit local-development mode"
        raise NatsAuthRequiredError(msg)

    return options


async def bind_existing_pull_consumer(
    js: JetStreamPullPort,
    *,
    settings: TrackingSettings,
) -> tuple[Any, Any]:
    """Bind to infra-provisioned durable — never create or mutate topology."""
    if settings.consumer_name != A1_DURABLE_CONSUMER:
        msg = (
            "Configured consumer name does not match A2 durable binding — "
            f"expected {A1_DURABLE_CONSUMER}, got {settings.consumer_name}"
        )
        raise ConsumerBindingMismatchError(msg)
    info = await js.consumer_info(A1_STREAM, A1_DURABLE_CONSUMER)
    verify_consumer_info(info)
    subscription = await js.pull_subscribe_bind(
        durable=A1_DURABLE_CONSUMER,
        stream=A1_STREAM,
    )
    bound_info = await subscription.consumer_info()
    verify_consumer_info(bound_info)
    return subscription, bound_info


async def verify_nats_readiness(
    js: JetStreamPullPort,
    *,
    settings: TrackingSettings,
) -> NatsConnectionReport:
    """Verify durable binding for readiness without starting the worker."""
    if settings.consumer_name != A1_DURABLE_CONSUMER:
        msg = (
            "Configured consumer name does not match A2 durable binding — "
            f"expected {A1_DURABLE_CONSUMER}, got {settings.consumer_name}"
        )
        raise ConsumerBindingMismatchError(msg)
    info = await js.consumer_info(A1_STREAM, A1_DURABLE_CONSUMER)
    verify_consumer_info(info)
    return NatsConnectionReport(
        connected=True,
        binding_verified=True,
        stream=A1_STREAM,
        durable_name=A1_DURABLE_CONSUMER,
    )


def log_connection_failure(exc: BaseException) -> None:
    """Log connection failures without URLs, credentials, or payloads."""
    if isinstance(exc, ConsumerBindingMismatchError):
        logger.error("tracking_nats_binding_mismatch", extra={"error_code": "BINDING_MISMATCH"})
        return
    if isinstance(exc, NatsAuthRequiredError):
        logger.error("tracking_nats_auth_required", extra={"error_code": "AUTH_REQUIRED"})
        return
    logger.error(
        "tracking_nats_connection_failed",
        extra={"error_code": type(exc).__name__},
    )
