"""Outbound channel transports.

Each channel is a port so that no transport credential or vendor SDK reaches the domain,
and so a service with nothing configured refuses to pretend it sent anything.

The delivery code is passed to `send` and never returned, stored or logged. A transport
that needs it receives it as an argument and is expected to forget it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from notification.domain.value_objects import Channel


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What a transport is asked to send.

    ``delivery_code`` is carried separately from ``body`` so that a transport which does
    not need it never sees it, and so that logging ``body`` cannot leak it.
    """

    channel: Channel
    recipient: str
    body: str
    delivery_code: str | None = None

    def redacted(self) -> str:
        """A form safe to log: no recipient, no code."""
        return f"<{self.channel.value} message, {len(self.body)} chars>"


@dataclass(frozen=True, slots=True)
class SendResult:
    accepted: bool
    provider_reference: str | None = None
    failure_reason: str | None = None


class ChannelTransport(Protocol):
    @property
    def channel(self) -> Channel: ...

    @property
    def is_production_ready(self) -> bool: ...

    def send(self, message: OutboundMessage) -> SendResult: ...


class TransportUnavailableError(RuntimeError):
    """The transport could not be reached — a platform fault, not a recipient's."""
