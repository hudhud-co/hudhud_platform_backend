"""A1 contract validation unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from event_envelope.serde import CONSUMER_SERDE_POLICY, deserialize_envelope

from tracking.application.validation import validate_a1_delivery
from tracking.domain.errors import ContractRejection
from tracking.domain.types import Delivery


def _validate(
    envelope: dict[str, Any],
    make_delivery: Callable[..., Delivery],
    **delivery_overrides: object,
) -> object:
    parsed = deserialize_envelope(json.dumps(envelope), policy=CONSUMER_SERDE_POLICY)
    delivery = make_delivery(envelope, **delivery_overrides)
    return validate_a1_delivery(envelope=parsed, delivery=delivery)


def test_valid_complete_envelope_is_accepted(
    make_envelope: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    validated = _validate(make_envelope(), make_delivery)
    assert validated.source_table == "shipment_events"  # type: ignore[union-attr]
    assert validated.actor_id is not None  # type: ignore[union-attr]
    assert "note" in validated.safe_metadata  # type: ignore[union-attr]


def test_wrong_stream_is_rejected(
    make_envelope: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    with pytest.raises(ContractRejection, match="stream"):
        _validate(make_envelope(), make_delivery, stream="HUDHUD_AUDIT")


def test_wrong_durable_is_rejected(
    make_envelope: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    with pytest.raises(ContractRejection, match="durable"):
        _validate(make_envelope(), make_delivery, consumer_name="audit_bridge_entry_v1")


def test_invented_aggregate_version_on_non_aggregate_fails_deserialize(
    make_envelope: Callable[..., dict[str, Any]],
) -> None:
    envelope = make_envelope(aggregate_version=4)
    with pytest.raises(Exception, match="aggregate_version"):
        deserialize_envelope(json.dumps(envelope), policy=CONSUMER_SERDE_POLICY)


def test_connection_string_metadata_is_rejected(
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = make_envelope(
        payload=make_payload(metadata={"dsn": "postgresql://user:pass@localhost/db"})
    )
    with pytest.raises(ContractRejection, match="secret-like"):
        _validate(envelope, make_delivery)


def test_raw_cdc_payload_fields_are_rejected(
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    payload = make_payload()
    payload["raw_row"] = {"id": "x"}
    envelope = make_envelope(payload=payload)
    with pytest.raises(ContractRejection, match="CDC"):
        _validate(envelope, make_delivery)


def test_wrong_source_table_is_rejected(
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
    make_delivery: Callable[..., Delivery],
) -> None:
    envelope = make_envelope(payload=make_payload(source_table="audit_logs"))
    with pytest.raises(ContractRejection, match="source_table"):
        _validate(envelope, make_delivery)
