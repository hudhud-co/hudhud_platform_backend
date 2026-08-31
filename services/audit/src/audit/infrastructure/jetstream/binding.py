"""Verify server-side durable binding — fail closed on topology mismatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from audit.domain.contract import A2_DURABLE_CONSUMER, A2_STREAM, A2_SUBJECT


class ConsumerBindingMismatchError(RuntimeError):
    """Raised when the server durable does not match the expected A2 binding."""


@dataclass(frozen=True, slots=True)
class ExpectedConsumerBinding:
    stream: str
    durable_name: str
    filter_subject: str


def expected_consumer_binding() -> ExpectedConsumerBinding:
    return ExpectedConsumerBinding(
        stream=A2_STREAM,
        durable_name=A2_DURABLE_CONSUMER,
        filter_subject=A2_SUBJECT,
    )


class ConsumerInfoView(Protocol):
    stream_name: str
    name: str
    config: Any


def verify_consumer_info(info: ConsumerInfoView) -> ExpectedConsumerBinding:
    """Fail closed when stream, durable, or filter subject do not match A2 binding."""
    expected = expected_consumer_binding()
    stream_name = getattr(info, "stream_name", None)
    durable_name = getattr(info, "name", None)
    config = getattr(info, "config", None)
    filter_subject = getattr(config, "filter_subject", None) if config is not None else None

    mismatches: list[str] = []
    if stream_name != expected.stream:
        mismatches.append("stream")
    if durable_name != expected.durable_name:
        mismatches.append("durable_name")
    if filter_subject != expected.filter_subject:
        mismatches.append("filter_subject")
    if mismatches:
        msg = (
            "JetStream durable binding mismatch — expected "
            f"stream={expected.stream}, durable={expected.durable_name}, "
            f"filter={expected.filter_subject}; mismatched fields: "
            f"{', '.join(mismatches)}"
        )
        raise ConsumerBindingMismatchError(msg)
    return expected
