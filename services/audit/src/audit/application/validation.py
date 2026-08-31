"""A2 envelope and payload validation for the Audit observation consumer."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.enums import AggregateScope, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.primitives import parse_utc_datetime
from messaging_conformance.observation import append_only_observation_event_id

from audit.domain.contract import (
    A2_DURABLE_CONSUMER,
    A2_EVENT_ID_NAMESPACE,
    A2_EVENT_TYPE,
    A2_EVENT_VERSION,
    A2_PRODUCER,
    A2_SOURCE_SYSTEM,
    A2_SOURCE_TABLE,
    A2_STREAM,
    A2_SUBJECT,
    FORBIDDEN_CDC_PAYLOAD_KEYS,
    FORBIDDEN_METADATA_KEYS,
    REQUIRED_PAYLOAD_FIELDS,
)
from audit.domain.errors import ContractRejection
from audit.domain.types import Delivery, LegacyAuditObservation, ValidatedA2Message

_JWT_VALUE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_API_KEY_VALUE = re.compile(r"(?i)sk_(live|test)_[A-Za-z0-9]+")
_CONNECTION_VALUE = re.compile(r"(?i)(postgres(ql)?|mysql|mongodb|redis|amqp)://")
_OTP_VALUE = re.compile(r"(?i)^(otp|one[_-]?time)[-_]?code$")


def validate_a2_delivery(*, envelope: EventEnvelope, delivery: Delivery) -> ValidatedA2Message:
    """Reject mismatched producer, type/version, subject, source table, or aggregate fields."""
    if delivery.subject != A2_SUBJECT:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered subject is not the A2 subject")
    if delivery.stream != A2_STREAM:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered stream is not HUDHUD_AUDIT")
    if delivery.consumer_name != A2_DURABLE_CONSUMER:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "durable consumer is not audit_bridge_entry_v1",
        )
    if envelope.event_type != A2_EVENT_TYPE:
        raise ContractRejection("SCHEMA_MISMATCH", "event_type is not the A2 observation type")
    if envelope.event_version != A2_EVENT_VERSION:
        raise ContractRejection("SCHEMA_MISMATCH", "event_version is not 1")
    if envelope.producer != A2_PRODUCER:
        raise ContractRejection("SCHEMA_MISMATCH", "producer is not legacy_bridge")
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
    if source_table != A2_SOURCE_TABLE:
        raise ContractRejection("SCHEMA_MISMATCH", "source_table is not audit_logs")

    source_pk = _parse_uuid(payload["source_pk"], field="source_pk")
    audit_entry_id = _parse_uuid(payload["audit_entry_id"], field="audit_entry_id")
    if audit_entry_id != source_pk:
        raise ContractRejection("SCHEMA_MISMATCH", "audit_entry_id must equal source_pk")

    expected_event_id = append_only_observation_event_id(
        A2_EVENT_ID_NAMESPACE,
        source_system=A2_SOURCE_SYSTEM,
        source_table=source_table,
        source_pk=str(source_pk),
    )
    if envelope.event_id != expected_event_id:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "event_id does not match append-only A2 identity",
        )

    safe_metadata = sanitize_observation_metadata(payload.get("metadata"))
    actor_id_raw = payload.get("actor_id")
    actor_id = _parse_uuid(actor_id_raw, field="actor_id") if actor_id_raw else None

    return ValidatedA2Message(
        event_id=envelope.event_id,
        correlation_id=envelope.correlation_id,
        source_system=A2_SOURCE_SYSTEM,
        source_table=source_table,
        source_pk=source_pk,
        source_position=str(payload["source_position"]),
        source_module=str(payload["source_module"]),
        audit_entry_id=audit_entry_id,
        action=str(payload["action"]),
        entity_type=str(payload["entity_type"]),
        entity_id=_parse_uuid(payload["entity_id"], field="entity_id"),
        actor_type=str(payload["actor_type"]),
        actor_id=actor_id,
        source=str(payload["source"]),
        occurred_at=_parse_occurred_at(payload["occurred_at"]),
        bridge_mapper_version=str(payload["bridge_mapper_version"]),
        safe_metadata=safe_metadata,
        event_type=envelope.event_type,
        event_version=envelope.event_version,
    )


def project_observation(
    message: ValidatedA2Message, *, received_at: datetime
) -> LegacyAuditObservation:
    """Map a validated A2 message to the observation projection (not a canonical fact)."""
    return LegacyAuditObservation(
        event_id=message.event_id,
        source_system=message.source_system,
        source_table=message.source_table,
        source_pk=message.source_pk,
        source_position=message.source_position,
        source_module=message.source_module,
        audit_entry_id=message.audit_entry_id,
        action=message.action,
        entity_type=message.entity_type,
        entity_id=message.entity_id,
        actor_type=message.actor_type,
        actor_id=message.actor_id,
        source=message.source,
        occurred_at=message.occurred_at,
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
