"""JetStream client protocol for fakes and live adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class JetStreamPubAck:
    """Minimal PubAck surface used by the Bridge publisher."""

    stream: str
    seq: int
    duplicate: bool = False


class JetStreamPublishClient(Protocol):
    """Publish-only JetStream boundary — no topology mutation."""

    def publish(
        self,
        *,
        subject: str,
        payload: bytes,
        msg_id: str,
        timeout: float,
    ) -> JetStreamPubAck:
        """Publish and return PubAck. Raise NatsPublishError subclasses on failure."""

    def ping(self) -> bool:
        """Return True when broker connectivity is healthy."""

    def drain(self) -> None:
        """Gracefully drain in-flight operations."""

    def close(self) -> None:
        """Close the underlying connection."""
