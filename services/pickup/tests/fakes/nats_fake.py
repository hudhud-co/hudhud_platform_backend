"""Fake JetStream client for deterministic relay tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from pickup.infrastructure.nats.errors import (
    NatsTemporaryError,
    NatsTimeoutError,
    StreamMismatchError,
    SubjectForbiddenError,
)
from pickup.infrastructure.nats.protocols import JetStreamPubAck
from pickup.infrastructure.nats.subjects import expected_stream_for_subject


@dataclass
class FakeJetStreamClient:
    """Records publishes without contacting a broker."""

    default_stream: str | None = None
    should_timeout: bool = False
    should_fail_transient: bool = False
    topology_calls: list[str] = field(default_factory=list)
    publish_log: list[tuple[str, bytes, str]] = field(default_factory=list)
    drained: bool = False
    closed: bool = False
    ping_ok: bool = True

    def publish(
        self,
        *,
        subject: str,
        payload: bytes,
        msg_id: str,
        timeout: float,
    ) -> JetStreamPubAck:
        _ = timeout
        if self.should_timeout:
            raise NatsTimeoutError("jetstream publish timed out")
        if self.should_fail_transient:
            raise NatsTemporaryError("broker unavailable")

        expected = expected_stream_for_subject(subject)
        if expected is None:
            raise SubjectForbiddenError(f"subject not allowlisted: {subject}")

        stream = self.default_stream or expected
        if stream != expected:
            raise StreamMismatchError(f"expected stream {expected}, received {stream}")

        self.publish_log.append((subject, payload, msg_id))
        return JetStreamPubAck(stream=stream, seq=len(self.publish_log), duplicate=False)

    def add_stream(self, *_args: object, **_kwargs: object) -> None:
        self.topology_calls.append("add_stream")

    def add_consumer(self, *_args: object, **_kwargs: object) -> None:
        self.topology_calls.append("add_consumer")

    def ping(self) -> bool:
        return self.ping_ok

    def drain(self) -> None:
        self.drained = True

    def close(self) -> None:
        self.closed = True
