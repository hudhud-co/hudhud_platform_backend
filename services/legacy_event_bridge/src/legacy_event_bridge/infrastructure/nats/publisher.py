"""JetStream publisher adapter implementing PublisherPort."""

from __future__ import annotations

from legacy_event_bridge.domain.publish import PublishResult
from legacy_event_bridge.domain.sanitize import sanitize_error_message
from legacy_event_bridge.infrastructure.nats.errors import NatsPublishError
from legacy_event_bridge.infrastructure.nats.protocols import JetStreamPublishClient
from legacy_event_bridge.infrastructure.nats.serialization import envelope_dict_to_wire_bytes


class JetStreamPublisherAdapter:
    """Maps JetStream PubAck outcomes to messaging_conformance publish results."""

    def __init__(
        self,
        client: JetStreamPublishClient,
        *,
        publish_timeout_seconds: float,
        transport_max_msg_bytes: int = 256 * 1024,
    ) -> None:
        self._client = client
        self._publish_timeout_seconds = publish_timeout_seconds
        self._transport_max_msg_bytes = transport_max_msg_bytes

    def publish(
        self,
        *,
        subject: str,
        payload_json: dict,
        transport_msg_id: str,
    ) -> PublishResult:
        payload = envelope_dict_to_wire_bytes(payload_json)
        if len(payload) > self._transport_max_msg_bytes:
            return PublishResult(
                ack_received=False,
                error_code="PAYLOAD_TOO_LARGE",
                error_message=(
                    f"serialized envelope exceeds transport limit "
                    f"({self._transport_max_msg_bytes} bytes)"
                ),
            )
        try:
            self._client.publish(
                subject=subject,
                payload=payload,
                msg_id=transport_msg_id,
                timeout=self._publish_timeout_seconds,
            )
        except NatsPublishError as exc:
            return PublishResult(
                ack_received=False,
                error_code=exc.error_code,
                error_message=exc.message,
            )
        except Exception as exc:
            return PublishResult(
                ack_received=False,
                error_code="NATS_TEMPORARY",
                error_message=sanitize_error_message(str(exc)),
            )
        return PublishResult(ack_received=True)

    def ping(self) -> bool:
        return self._client.ping()

    def drain(self) -> None:
        self._client.drain()

    def close(self) -> None:
        self._client.close()
