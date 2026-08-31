"""Deferred JetStream transport — coordinator runs off-loop; broker applies on-loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from messaging_conformance.enums import JetStreamConsumerAction

from audit.domain.types import Delivery
from audit.infrastructure.jetstream.broker import JetStreamBrokerAckClient
from audit.ports import ConsumerTransportPort


@dataclass
class PendingTransportAction:
    action: JetStreamConsumerAction
    delivery: Delivery


@dataclass
class DeferredJetStreamTransport(ConsumerTransportPort):
    """Record transport decisions during sync coordinator handling."""

    pending: list[PendingTransportAction] = field(default_factory=list)

    def ack(self, delivery: Delivery) -> None:
        self.pending.append(PendingTransportAction(JetStreamConsumerAction.ACK, delivery))

    def nak(self, delivery: Delivery) -> None:
        self.pending.append(PendingTransportAction(JetStreamConsumerAction.NAK, delivery))

    def defer(self, delivery: Delivery) -> None:
        self.pending.append(PendingTransportAction(JetStreamConsumerAction.DEFER, delivery))


async def flush_transport_actions(
    broker: JetStreamBrokerAckClient,
    actions: list[PendingTransportAction],
) -> None:
    """Apply queued broker actions on the worker event loop after commit."""
    for item in actions:
        if item.action is JetStreamConsumerAction.ACK:
            await broker.apply_ack(item.delivery)
        elif item.action is JetStreamConsumerAction.NAK:
            await broker.apply_nak(item.delivery)
        else:
            await broker.apply_defer(item.delivery)
