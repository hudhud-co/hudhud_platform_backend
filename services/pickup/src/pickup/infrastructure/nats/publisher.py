"""JetStream publisher adapter implementing PublisherPort."""

from __future__ import annotations

from typing import Any

from pickup.domain.errors import EnvelopeContractValidationFailed
from pickup.domain.publish import PublishResult
from pickup.domain.sanitize import sanitize_error_message
from pickup.infrastructure.contracts.registry import validate_accepted_fact_envelope
from pickup.infrastructure.nats.errors import (
    EnvelopeInvalidError,
    NatsPublishError,
    SubjectForbiddenError,
)
from pickup.infrastructure.nats.protocols import JetStreamPublishClient
from pickup.infrastructure.nats.serialization import envelope_dict_to_wire_bytes
from pickup.infrastructure.nats.subjects import validate_subject_allowed


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
        payload_json: dict[str, Any],
        transport_msg_id: str,
    ) -> PublishResult:
        try:
            validate_subject_allowed(subject)
        except ValueError as exc:
            return PublishResult(
                ack_received=False,
                error_code="SUBJECT_FORBIDDEN",
                error_message=sanitize_error_message(str(exc)),
            )

        try:
            validate_accepted_fact_envelope(payload_json)
        except EnvelopeContractValidationFailed as exc:
            return PublishResult(
                ack_received=False,
                error_code="ENVELOPE_INVALID",
                error_message=sanitize_error_message(str(exc)),
            )
        except Exception as exc:
            return PublishResult(
                ack_received=False,
                error_code="ENVELOPE_INVALID",
                error_message=sanitize_error_message(str(exc)),
            )

        try:
            payload = envelope_dict_to_wire_bytes(payload_json)
        except Exception as exc:
            return PublishResult(
                ack_received=False,
                error_code="ENVELOPE_INVALID",
                error_message=sanitize_error_message(str(exc)),
            )

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
        except (SubjectForbiddenError, EnvelopeInvalidError) as exc:
            return PublishResult(
                ack_received=False,
                error_code=exc.error_code,
                error_message=exc.message,
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
