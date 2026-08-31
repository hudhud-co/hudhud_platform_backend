"""Live NATS JetStream connection — no topology mutation."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

from nats.aio.client import Client as NatsClient
from nats.js.api import PubAck

from legacy_event_bridge.config import BridgeSettings, RuntimeEnvironment
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.infrastructure.nats.errors import (
    NatsAclDeniedError,
    NatsNotConfiguredError,
    NatsTemporaryError,
    NatsTimeoutError,
    StreamMismatchError,
    SubjectForbiddenError,
)
from legacy_event_bridge.infrastructure.nats.protocols import JetStreamPubAck
from legacy_event_bridge.infrastructure.nats.subjects import (
    expected_stream_for_subject,
    validate_subject_allowed,
)


def assert_nats_configuration(settings: BridgeSettings) -> None:
    """Validate NATS settings before opening a live connection."""
    if not settings.relay_enabled:
        raise NatsNotConfiguredError("relay is disabled")
    if not settings.nats_url:
        raise NatsNotConfiguredError("NATS URL is not configured")

    if settings.environment is RuntimeEnvironment.PRODUCTION:
        if not settings.adr_0004_credentials_configured:
            raise NatsNotConfiguredError("ADR-0004 credentials gate is not satisfied")
        if not settings.nats_tls_enabled:
            raise NatsNotConfiguredError("production requires explicit NATS TLS")
        if not _has_credentials(settings) and not settings.nats_dev_no_auth:
            raise NatsNotConfiguredError("production requires NATS credentials")

    if settings.nats_dev_no_auth:
        if settings.environment is RuntimeEnvironment.PRODUCTION:
            raise NatsNotConfiguredError("no-auth mode is forbidden in production")
        return

    if not _has_credentials(settings):
        raise NatsNotConfiguredError(
            "NATS credentials missing — set token or user/password, or enable dev no-auth flag"
        )


def _has_credentials(settings: BridgeSettings) -> bool:
    return bool(settings.nats_token or (settings.nats_user and settings.nats_password))


class LiveNatsJetStreamClient:
    """Async NATS JetStream client wrapped for sync relay use."""

    def __init__(self, settings: BridgeSettings) -> None:
        assert_nats_configuration(settings)
        self._settings = settings
        self._loop = asyncio.new_event_loop()
        self._client: NatsClient | None = None
        self._js: Any = None

    def connect(self) -> None:
        self._run(self._async_connect())

    def publish(
        self,
        *,
        subject: str,
        payload: bytes,
        msg_id: str,
        timeout: float,
    ) -> JetStreamPubAck:
        return self._run(
            self._async_publish(
                subject=subject,
                payload=payload,
                msg_id=msg_id,
                timeout=timeout,
            )
        )

    def ping(self) -> bool:
        try:
            return self._run(self._async_ping())
        except Exception:
            return False

    def drain(self) -> None:
        if self._client is not None:
            self._run(self._async_drain())

    def close(self) -> None:
        if self._client is not None:
            self._run(self._async_close())
        self._loop.close()

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    async def _async_connect(self) -> None:
        if self._client is not None:
            return
        connect_kwargs: dict[str, Any] = {
            "servers": [self._settings.nats_url],
            "connect_timeout": self._settings.nats_connect_timeout_seconds,
            "error_cb": None,
        }
        if self._settings.nats_user and self._settings.nats_password:
            connect_kwargs["user"] = self._settings.nats_user
            connect_kwargs["password"] = self._settings.nats_password
        if self._settings.nats_token:
            connect_kwargs["token"] = self._settings.nats_token
        if self._settings.nats_tls_enabled:
            connect_kwargs["tls"] = _build_tls_context(self._settings)
        self._client = await NatsClient().connect(**connect_kwargs)
        self._js = self._client.jetstream()

    async def _async_ping(self) -> bool:
        if self._client is None:
            await self._async_connect()
        assert self._client is not None
        await self._client.flush()
        return True

    async def _async_publish(
        self,
        *,
        subject: str,
        payload: bytes,
        msg_id: str,
        timeout: float,
    ) -> JetStreamPubAck:
        if self._js is None:
            await self._async_connect()
        assert self._js is not None
        try:
            validate_subject_allowed(subject)
        except ValueError as exc:
            raise SubjectForbiddenError(sanitize_error_message(str(exc))) from exc

        expected_stream = expected_stream_for_subject(subject)
        if expected_stream is None:
            raise SubjectForbiddenError("subject has no stream mapping")

        headers = {"Nats-Msg-Id": msg_id}
        try:
            ack: PubAck = await self._js.publish(
                subject,
                payload,
                headers=headers,
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise NatsTimeoutError("jetstream publish timed out") from exc
        except Exception as exc:
            message = sanitize_error_message(str(exc))
            lowered = message.lower()
            if "authorization" in lowered or "permission" in lowered or "auth" in lowered:
                raise NatsAclDeniedError(message) from exc
            if "timeout" in lowered:
                raise NatsTimeoutError(message) from exc
            raise NatsTemporaryError(message) from exc

        if ack.stream != expected_stream:
            raise StreamMismatchError(
                f"expected stream {expected_stream}, received {ack.stream}",
            )

        return JetStreamPubAck(stream=ack.stream, seq=ack.seq, duplicate=ack.duplicate)

    async def _async_drain(self) -> None:
        if self._client is not None:
            await self._client.drain()

    async def _async_close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._js = None


def _build_tls_context(settings: BridgeSettings) -> ssl.SSLContext:
    if settings.nats_tls_ca_file:
        return ssl.create_default_context(cafile=settings.nats_tls_ca_file)
    return ssl.create_default_context()


def build_live_nats_client(settings: BridgeSettings) -> LiveNatsJetStreamClient:
    """Construct a live NATS client after configuration gates pass."""
    settings.assert_production_gates()
    assert_nats_configuration(settings)
    return LiveNatsJetStreamClient(settings)
