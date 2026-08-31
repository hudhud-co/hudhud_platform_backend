"""Local JetStream transport adapter — no embedded credentials; not started by tests."""

from __future__ import annotations

from audit.config import AuditSettings
from audit.domain.types import Delivery
from audit.ports import BrokerAckClient, ConsumerTransportPort


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


def build_local_jetstream_transport(
    settings: AuditSettings,
    *,
    client: BrokerAckClient | None = None,
) -> ConsumerTransportPort:
    """Build a local adapter only when explicitly enabled and a client is injected.

    Production NATS credentials remain blocked by ADR-0004. This factory never
    embeds credentials and never constructs a live NATS connection.
    """
    if not settings.nats_enabled:
        raise TransportNotConfiguredError("NATS transport is disabled")
    if not settings.nats_url:
        raise TransportNotConfiguredError("NATS URL is not configured")
    if client is None:
        raise TransportNotConfiguredError(
            "NATS client must be injected explicitly; ADR-0004 credentials are not configured"
        )
    return AckAfterCommitTransport(client)
