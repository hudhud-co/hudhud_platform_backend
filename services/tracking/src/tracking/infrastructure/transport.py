"""Local JetStream transport adapter — no embedded credentials; not started by tests."""

from __future__ import annotations

from tracking.config import TrackingSettings
from tracking.domain.types import Delivery
from tracking.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from tracking.ports import BrokerAckClient, ConsumerTransportPort


class TransportNotConfiguredError(RuntimeError):
    """Raised when NATS transport is requested without explicit configuration."""


class AckAfterCommitTransport:
    """Delegates ACK/NAK/DEFER to an injected broker client after application commit."""

    def __init__(self, client: BrokerAckClient) -> None:
        self._client = client

    def ack(self, delivery: Delivery) -> None:
        self._client.ack(delivery)

    def nak(self, delivery: Delivery) -> None:
        self._client.nak(delivery)

    def defer(self, delivery: Delivery) -> None:
        self._client.defer(delivery)


def build_jetstream_transport(
    settings: TrackingSettings,
    *,
    client: BrokerAckClient | None = None,
    loop: object | None = None,
) -> ConsumerTransportPort:
    """Build JetStream ACK transport when explicitly enabled.

    Production NATS credentials remain blocked by ADR-0010 unless configured.
    This factory never embeds credentials.
    """
    if not settings.nats_enabled:
        raise TransportNotConfiguredError("NATS transport is disabled")
    if not settings.nats_url:
        raise TransportNotConfiguredError("NATS URL is not configured")
    if client is None:
        if loop is None:
            raise TransportNotConfiguredError(
                "NATS client must be injected explicitly; ADR-0010 credentials are not configured"
            )
        client = JetStreamBrokerAckClient(
            loop=loop,  # type: ignore[arg-type]
            defer_delay_seconds=settings.defer_delay_seconds,
        )
    return AckAfterCommitTransport(client)


def build_local_jetstream_transport(
    settings: TrackingSettings,
    *,
    client: BrokerAckClient | None = None,
) -> ConsumerTransportPort:
    """Backward-compatible alias for explicit test injection."""
    return build_jetstream_transport(settings, client=client)