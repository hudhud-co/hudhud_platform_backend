"""Transport adapter configuration tests — no live NATS."""

from __future__ import annotations

import pytest

from tracking.config import RuntimeEnvironment, load_settings
from tracking.domain.types import Delivery
from tracking.infrastructure.transport import (
    AckAfterCommitTransport,
    TransportNotConfiguredError,
    build_local_jetstream_transport,
)


class _FakeBroker:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def ack(self, _delivery: Delivery) -> None:
        self.actions.append("ack")

    def nak(self, _delivery: Delivery) -> None:
        self.actions.append("nak")

    def defer(self, _delivery: Delivery) -> None:
        self.actions.append("defer")


def test_transport_requires_explicit_enable() -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    with pytest.raises(TransportNotConfiguredError, match="disabled"):
        build_local_jetstream_transport(settings)


def test_transport_requires_url_and_injected_client() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url="",
    )
    with pytest.raises(TransportNotConfiguredError, match="URL"):
        build_local_jetstream_transport(settings)

    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        nats_enabled=True,
        nats_url="nats://localhost:4222",
    )
    with pytest.raises(TransportNotConfiguredError, match="injected"):
        build_local_jetstream_transport(settings)


def test_ack_after_commit_wrapper_delegates() -> None:
    broker = _FakeBroker()
    transport = AckAfterCommitTransport(broker)
    delivery = Delivery(
        body=b"{}",
        subject="x",
        stream="HUDHUD_SHIPMENT",
        consumer_name="tracking_bridge_timeline_v1",
    )
    transport.ack(delivery)
    transport.nak(delivery)
    transport.defer(delivery)
    assert broker.actions == ["ack", "nak", "defer"]
