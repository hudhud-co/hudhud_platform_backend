"""Map JetStream messages to transport-independent Delivery views."""

from __future__ import annotations

from typing import Any

from tracking.domain.contract import A1_DURABLE_CONSUMER, A1_STREAM
from tracking.domain.types import Delivery


def delivery_from_message(
    msg: Any,
    *,
    stream: str = A1_STREAM,
    consumer_name: str = A1_DURABLE_CONSUMER,
) -> Delivery:
    """Extract delivery metadata without logging payload or credentials."""
    jetstream_seq: int | None = None
    metadata = getattr(msg, "metadata", None)
    if metadata is not None:
        sequence = getattr(metadata, "sequence", None)
        if sequence is not None:
            jetstream_seq = getattr(sequence, "stream", None)

    nats_msg_id: str | None = None
    headers = getattr(msg, "headers", None)
    if headers is not None:
        raw = headers.get("Nats-Msg-Id")
        if raw is not None:
            nats_msg_id = str(raw)

    subject = str(getattr(msg, "subject", ""))
    body = getattr(msg, "data", b"")
    if not isinstance(body, (bytes, bytearray)):
        body = bytes(body)

    return Delivery(
        body=bytes(body),
        subject=subject,
        stream=stream,
        consumer_name=consumer_name,
        nats_msg_id=nats_msg_id,
        jetstream_seq=jetstream_seq,
        transport_handle=msg,
    )
