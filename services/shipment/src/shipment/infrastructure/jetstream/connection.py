"""NATS connection options and durable bind — no topology mutation."""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any, Protocol

from shipment.config import RuntimeEnvironment, ShipmentSettings
from shipment.infrastructure.jetstream.binding import (
    CONSUMER_BINDINGS,
    ConsumerBindingMismatchError,
    verify_consumer_info,
)

logger = logging.getLogger("shipment.jetstream.connection")


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


def build_nats_connect_options(settings: ShipmentSettings) -> dict[str, Any]:
    """Build nats.connect kwargs — credentials from environment only."""
    if not settings.nats_url:
        msg = "NATS URL is not configured"
        raise RuntimeError(msg)

    options: dict[str, Any] = {"servers": [settings.nats_url]}

    if settings.nats_tls_enabled:
        options["tls"] = _build_tls_context(settings)

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
    secured = settings.environment in {
        RuntimeEnvironment.STAGING,
        RuntimeEnvironment.PRODUCTION,
    }

    if settings.environment in {RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST}:
        if settings.allow_no_auth_local or has_credentials:
            return options
        msg = "Local NATS without auth requires SHIPMENT_ALLOW_NO_AUTH_LOCAL"
        raise NatsAuthRequiredError(msg)

    if secured and not has_credentials:
        msg = "Staging/production NATS requires ADR-0010 credential gate"
        raise NatsAuthRequiredError(msg)

    if secured and not settings.nats_tls_enabled:
        msg = "Staging/production NATS requires TLS"
        raise NatsAuthRequiredError(msg)

    if not has_credentials:
        msg = "NATS credentials are required outside explicit local-development mode"
        raise NatsAuthRequiredError(msg)

    return options


async def bind_existing_pull_consumer(
    js: JetStreamPullPort,
    *,
    settings: ShipmentSettings,
) -> tuple[Any, Any]:
    """Bind to infra-provisioned durable — never create or mutate topology."""
    durable, stream = _resolve_binding(settings)
    info = await js.consumer_info(stream, durable)
    verify_consumer_info(info, durable_name=durable)
    subscription = await js.pull_subscribe_bind(durable=durable, stream=stream)
    bound_info = await subscription.consumer_info()
    verify_consumer_info(bound_info, durable_name=durable)
    return subscription, bound_info


def _resolve_binding(settings: ShipmentSettings) -> tuple[str, str]:
    """Resolve the single durable this worker instance serves — never create topology."""
    binding = CONSUMER_BINDINGS.get(settings.consumer_name)
    if binding is None:
        known = ", ".join(sorted(CONSUMER_BINDINGS))
        msg = (
            "Configured consumer name does not match a registered Pickup durable "
            f"binding — expected one of {known}, got {settings.consumer_name}"
        )
        raise ConsumerBindingMismatchError(msg)
    stream, _subject = binding
    return settings.consumer_name, stream


async def verify_nats_readiness(
    js: JetStreamPullPort,
    *,
    settings: ShipmentSettings,
) -> NatsConnectionReport:
    """Verify durable binding for readiness without starting the worker."""
    durable, stream = _resolve_binding(settings)
    info = await js.consumer_info(stream, durable)
    verify_consumer_info(info, durable_name=durable)
    return NatsConnectionReport(
        connected=True,
        binding_verified=True,
        stream=stream,
        durable_name=durable,
    )


def _build_tls_context(settings: ShipmentSettings) -> ssl.SSLContext:
    if settings.nats_tls_ca_file:
        return ssl.create_default_context(cafile=settings.nats_tls_ca_file)
    return ssl.create_default_context()


def log_connection_failure(exc: BaseException) -> None:
    """Log connection failures without URLs, credentials, or payloads."""
    if isinstance(exc, ConsumerBindingMismatchError):
        logger.error("shipment_nats_binding_mismatch", extra={"error_code": "BINDING_MISMATCH"})
        return
    if isinstance(exc, NatsAuthRequiredError):
        logger.error("shipment_nats_auth_required", extra={"error_code": "AUTH_REQUIRED"})
        return
    logger.error(
        "shipment_nats_connection_failed",
        extra={"error_code": type(exc).__name__},
    )
