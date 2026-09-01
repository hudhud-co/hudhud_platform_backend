"""A1 envelope and payload validation for the Tracking timeline consumer."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.enums import AggregateScope, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.primitives import parse_utc_datetime
from messaging_conformance.observation import append_only_observation_event_id

from tracking.domain.contract import (
    A1_DURABLE_CONSUMER,
    A1_EVENT_ID_NAMESPACE,
    A1_EVENT_TYPE,
    A1_EVENT_VERSION,
    A1_PRODUCER,
    A1_SCHEMA_URI,
    A1_SOURCE_SYSTEM,
    A1_SOURCE_TABLE,
    A1_STREAM,
    A1_SUBJECT,
    FORBIDDEN_CDC_PAYLOAD_KEYS,
    FORBIDDEN_METADATA_KEYS,
    REQUIRED_PAYLOAD_FIELDS,
)
from tracking.domain.errors import ContractRejection
from tracking.domain.types import Delivery, ShipmentTimelineEntry, ValidatedA1Message

_JWT_VALUE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_API_KEY_VALUE = re.compile(r"(?i)sk_(live|test)_[A-Za-z0-9]+")
_CONNECTION_VALUE = re.compile(r"(?i)(postgres(ql)?|mysql|mongodb|redis|amqp)://")
_OTP_VALUE = re.compile(r"(?i)^(otp|one[_-]?time)[-_]?code$")


def validate_a1_delivery(*, envelope: EventEnvelope, delivery: Delivery) -> ValidatedA1Message:
    """Reject mismatched producer, type/version, subject, source table, or aggregate fields."""
    if delivery.subject != A1_SUBJECT:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered subject is not the A1 subject")
    if delivery.stream != A1_STREAM:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered stream is not HUDHUD_SHIPMENT")
    if delivery.consumer_name != A1_DURABLE_CONSUMER:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "durable consumer is not tracking_bridge_timeline_v1",
        )
    if envelope.event_type != A1_EVENT_TYPE:
        raise ContractRejection("SCHEMA_MISMATCH", "event_type is not the A1 observation type")
    if envelope.event_version != A1_EVENT_VERSION:
        raise ContractRejection("SCHEMA_MISMATCH", "event_version is not 1")
    if envelope.producer != A1_PRODUCER:
        raise ContractRejection("SCHEMA_MISMATCH", "producer is not legacy_bridge")
    if envelope.schema_uri is not None and envelope.schema_uri != A1_SCHEMA_URI:
        raise ContractRejection("SCHEMA_MISMATCH", "schema_uri does not match A1 registry")
    if envelope.message_kind is not MessageKind.INTEGRATION:
        raise ContractRejection("SCHEMA_MISMATCH", "message_kind is not integration")
    if envelope.aggregate_scope is not AggregateScope.NON_AGGREGATE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_scope must be non_aggregate")
    if envelope.aggregate_type is not None or envelope.aggregate_id is not None:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate identity must be absent")
    if envelope.aggregate_version is not None:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_version must not be invented")

    payload = envelope.payload
    _reject_cdc_fields(payload)
    for field_name in REQUIRED_PAYLOAD_FIELDS:
        if field_name not in payload or payload[field_name] in (None, ""):
            raise ContractRejection(
                "SCHEMA_MISMATCH",
                f"missing required payload field {field_name}",
            )

    source_table = str(payload["source_table"])
    if source_table != A1_SOURCE_TABLE:
        raise ContractRejection("SCHEMA_MISMATCH", "source_table is not shipment_events")

    source_pk = _parse_uuid(payload["source_pk"], field="source_pk")
    shipment_id = _parse_uuid(payload["shipment_id"], field="shipment_id")

    expected_event_id = append_only_observation_event_id(
        A1_EVENT_ID_NAMESPACE,
        source_system=A1_SOURCE_SYSTEM,
        source_table=source_table,
        source_pk=str(source_pk),
    )
    if envelope.event_id != expected_event_id:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "event_id does not match append-only A1 identity",
        )

    safe_metadata = sanitize_observation_metadata(payload.get("metadata"))
    actor_type_raw = payload.get("actor_type")
    actor_id_raw = payload.get("actor_id")
    actor_type = str(actor_type_raw) if actor_type_raw not in (None, "") else None
    actor_id = _parse_uuid(actor_id_raw, field="actor_id") if actor_id_raw else None
    if actor_id is not None and actor_type is None:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "actor_type is required when actor_id is present",
        )

    old_status = _optional_string(payload.get("old_status"))
    new_status = _optional_string(payload.get("new_status"))

    return ValidatedA1Message(
        event_id=envelope.event_id,
        correlation_id=envelope.correlation_id,
        shipment_id=shipment_id,
        source_system=A1_SOURCE_SYSTEM,
        source_table=source_table,
        source_pk=source_pk,
        source_position=str(payload["source_position"]),
        source_module=str(payload["source_module"]),
        legacy_event_type=str(payload["legacy_event_type"]),
        occurred_at=_parse_occurred_at(payload["occurred_at"]),
        old_status=old_status,
        new_status=new_status,
        actor_type=actor_type,
        actor_id=actor_id,
        bridge_mapper_version=str(payload["bridge_mapper_version"]),
        safe_metadata=safe_metadata,
        event_type=envelope.event_type,
        event_version=envelope.event_version,
    )


def project_timeline_entry(
    message: ValidatedA1Message, *, received_at: datetime
) -> ShipmentTimelineEntry:
    """Map a validated A1 message to the timeline projection (not canonical Shipment state)."""
    return ShipmentTimelineEntry(
        event_id=message.event_id,
        shipment_id=message.shipment_id,
        source_system=message.source_system,
        source_table=message.source_table,
        source_pk=message.source_pk,
        source_position=message.source_position,
        source_module=message.source_module,
        legacy_event_type=message.legacy_event_type,
        occurred_at=message.occurred_at,
        old_status=message.old_status,
        new_status=message.new_status,
        actor_type=message.actor_type,
        actor_id=message.actor_id,
        bridge_mapper_version=message.bridge_mapper_version,
        safe_metadata=dict(message.safe_metadata),
        received_at=received_at,
    )


def sanitize_observation_metadata(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractRejection("SCHEMA_MISMATCH", "metadata must be an object")
    if len(value) > 32:
        raise ContractRejection("SCHEMA_MISMATCH", "metadata exceeds allowlisted size")
    cleaned: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized = key.strip().lower().replace("-", "_")
        if normalized in FORBIDDEN_METADATA_KEYS or _OTP_VALUE.match(normalized):
            raise ContractRejection("SCHEMA_MISMATCH", "metadata contains a forbidden secret key")
        _reject_secret_like_value(raw_value)
        cleaned[key] = raw_value
    return cleaned


def _reject_cdc_fields(payload: dict[str, Any]) -> None:
    present = FORBIDDEN_CDC_PAYLOAD_KEYS.intersection(payload)
    if present:
        raise ContractRejection("SCHEMA_MISMATCH", "payload contains raw CDC fields")


def _reject_secret_like_value(value: object) -> None:
    if isinstance(value, str):
        secret_like = (
            _JWT_VALUE.search(value)
            or _API_KEY_VALUE.search(value)
            or _CONNECTION_VALUE.search(value)
        )
        if secret_like:
            raise ContractRejection("SCHEMA_MISMATCH", "metadata contains a secret-like value")
        return
    if isinstance(value, list):
        for item in value:
            _reject_secret_like_value(item)


def _parse_uuid(value: object, *, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} is not a UUID") from exc


def _parse_occurred_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return parse_utc_datetime(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", "occurred_at is not RFC 3339 UTC") from exc


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
