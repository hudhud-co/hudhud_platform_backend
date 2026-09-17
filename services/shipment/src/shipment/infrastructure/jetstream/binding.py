"""Verify server-side durable binding — fail closed on topology mismatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shipment.domain.contract import (
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_STREAM,
    PICKUP_ACCEPTED_SUBJECT,
    PICKUP_HANDOVER_DURABLE_CONSUMER,
    PICKUP_HANDOVER_STREAM,
    PICKUP_HANDOVER_SUBJECT,
)

EXPECTED_ACK_POLICY = "explicit"

#: One durable binding per consumed Pickup contract. Each worker instance serves
#: exactly one; the stream is shared, the durable and filter subject are not.
CONSUMER_BINDINGS: dict[str, tuple[str, str]] = {
    PICKUP_ACCEPTED_DURABLE_CONSUMER: (PICKUP_ACCEPTED_STREAM, PICKUP_ACCEPTED_SUBJECT),
    PICKUP_HANDOVER_DURABLE_CONSUMER: (PICKUP_HANDOVER_STREAM, PICKUP_HANDOVER_SUBJECT),
}


class ConsumerBindingMismatchError(RuntimeError):
    """Raised when the server durable does not match the expected binding."""


@dataclass(frozen=True, slots=True)
class ExpectedConsumerBinding:
    stream: str
    durable_name: str
    filter_subject: str
    ack_policy: str = EXPECTED_ACK_POLICY


def expected_consumer_binding(
    durable_name: str = PICKUP_ACCEPTED_DURABLE_CONSUMER,
) -> ExpectedConsumerBinding:
    binding = CONSUMER_BINDINGS.get(durable_name)
    if binding is None:
        msg = f"unknown Shipment durable consumer: {durable_name}"
        raise ConsumerBindingMismatchError(msg)
    stream, filter_subject = binding
    return ExpectedConsumerBinding(
        stream=stream,
        durable_name=durable_name,
        filter_subject=filter_subject,
        ack_policy=EXPECTED_ACK_POLICY,
    )


class ConsumerInfoView(Protocol):
    stream_name: str
    name: str
    config: Any


def verify_consumer_info(
    info: ConsumerInfoView,
    *,
    durable_name: str = PICKUP_ACCEPTED_DURABLE_CONSUMER,
) -> ExpectedConsumerBinding:
    """Fail closed when stream, durable, filter, AckPolicy, or identity mismatch."""
    expected = expected_consumer_binding(durable_name)
    stream_name = getattr(info, "stream_name", None)
    actual_durable = getattr(info, "name", None)
    config = getattr(info, "config", None)
    filter_subject = _extract_filter_subject(config)
    ack_policy = _normalize_ack_policy(
        getattr(config, "ack_policy", None) if config is not None else None
    )

    mismatches: list[str] = []
    if stream_name != expected.stream:
        mismatches.append("stream")
    if actual_durable != expected.durable_name:
        mismatches.append("durable_name")
    if filter_subject != expected.filter_subject:
        mismatches.append("filter_subject")
    if ack_policy != expected.ack_policy:
        mismatches.append("ack_policy")
    if actual_durable not in CONSUMER_BINDINGS:
        mismatches.append("server_side_identity")
    if mismatches:
        # Deduplicate while preserving order
        unique = list(dict.fromkeys(mismatches))
        msg = (
            "JetStream durable binding mismatch — expected "
            f"stream={expected.stream}, durable={expected.durable_name}, "
            f"filter={expected.filter_subject}, ack_policy={expected.ack_policy}; "
            f"mismatched fields: {', '.join(unique)}"
        )
        raise ConsumerBindingMismatchError(msg)
    return expected


def _extract_filter_subject(config: Any) -> str | None:
    if config is None:
        return None
    filter_subject = getattr(config, "filter_subject", None)
    if filter_subject:
        return str(filter_subject)
    filter_subjects = getattr(config, "filter_subjects", None)
    if not filter_subjects:
        return None
    subjects = [str(item) for item in filter_subjects]
    if len(subjects) != 1:
        return None
    return subjects[0]


def _normalize_ack_policy(raw: object) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    text = str(value).strip().lower()
    if text.startswith("ackpolicy."):
        text = text.split(".", 1)[1]
    return text
