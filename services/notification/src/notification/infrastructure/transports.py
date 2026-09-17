"""Channel transport adapters.

The default for every channel is `UnavailableTransport`: a deployment with no SMS provider
configured must refuse to send rather than record a message as sent. Marking something
delivered that never left the building is the failure that costs a receiver their parcel.

`RecordingTransport` is for tests. `ConsoleTransport` is a development aid and says so —
it is never production-ready, and it prints the body of non-code-bearing messages only.
"""

from __future__ import annotations

import logging

from notification.domain.value_objects import Channel
from notification.ports.transport import (
    OutboundMessage,
    SendResult,
    TransportUnavailableError,
)

logger = logging.getLogger(__name__)


class UnavailableTransport:
    """The composition default. Refuses rather than pretending."""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def is_production_ready(self) -> bool:
        return False

    def send(self, message: OutboundMessage) -> SendResult:
        _ = message
        msg = f"no transport configured for {self._channel.value}"
        raise TransportUnavailableError(msg)


class RecordingTransport:
    """Test-only transport that keeps what it was asked to send."""

    def __init__(self, channel: Channel, *, accept: bool = True) -> None:
        self._channel = channel
        self._accept = accept
        self.sent: list[OutboundMessage] = []

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def is_production_ready(self) -> bool:
        return False

    def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        if not self._accept:
            return SendResult(accepted=False, failure_reason="provider_rejected")
        return SendResult(accepted=True, provider_reference="recorded")


class ConsoleTransport:
    """Development aid. Never production-ready, and never prints a delivery code."""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def is_production_ready(self) -> bool:
        return False

    def send(self, message: OutboundMessage) -> SendResult:
        # Log the redacted form only: the body of a code-bearing message is exactly what
        # must not end up in a log file or a terminal scrollback.
        logger.info("notification.console %s", message.redacted())
        return SendResult(accepted=True, provider_reference="console")
