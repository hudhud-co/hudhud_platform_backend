"""pickup.fact.accepted envelope and payload validation for Shipment inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.enums import AggregateScope, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.media_refs import MediaRef
from event_envelope.primitives import parse_utc_datetime

from shipment.domain.contract import (
    ALLOWED_OUTCOMES,
    FORBIDDEN_PAYLOAD_KEYS,
    PICKUP_ACCEPTED_AGGREGATE_SCOPE,
    PICKUP_ACCEPTED_AGGREGATE_TYPE,
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_EVENT_TYPE,
    PICKUP_ACCEPTED_EVENT_VERSION,
    PICKUP_ACCEPTED_MESSAGE_KIND,
    PICKUP_ACCEPTED_PRODUCER,
    PICKUP_ACCEPTED_SCHEMA_URI,
    PICKUP_ACCEPTED_STREAM,
    PICKUP_ACCEPTED_SUBJECT,
    PLACEHOLDER_DRIVER_IDENTITIES,
    REQUIRED_PAYLOAD_FIELDS,
)
from shipment.domain.errors import ContractRejection
from shipment.domain.types import Delivery, ValidatedPickupAcceptedFact
from shipment.domain.value_objects import AcceptanceOutcome, EvidenceReference

_OUTCOME_MAP: dict[str, AcceptanceOutcome] = {
    "ACCEPTED": AcceptanceOutcome.ACCEPTED,
    "ACCEPTED_WITH_EXCEPTION": AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
}


def validate_pickup_accepted_delivery(
    *,
    envelope: EventEnvelope,
    delivery: Delivery,
) -> ValidatedPickupAcceptedFact:
    """Fail closed on contract/envelope mismatches before any domain work."""
    if delivery.subject != PICKUP_ACCEPTED_SUBJECT:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "delivered subject is not pickup.fact.accepted",
        )
    if delivery.stream and delivery.stream != PICKUP_ACCEPTED_STREAM:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered stream is not HUDHUD_PICKUP")
    if delivery.consumer_name and delivery.consumer_name != PICKUP_ACCEPTED_DURABLE_CONSUMER:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "durable consumer is not shipment_pickup_facts_v1",
        )
    if envelope.event_type != PICKUP_ACCEPTED_EVENT_TYPE:
        raise ContractRejection("SCHEMA_MISMATCH", "event_type is not pickup.fact.accepted")
    if envelope.event_version != PICKUP_ACCEPTED_EVENT_VERSION:
        raise ContractRejection("SCHEMA_MISMATCH", "event_version is not 1")
    if envelope.producer != PICKUP_ACCEPTED_PRODUCER:
        raise ContractRejection("SCHEMA_MISMATCH", "producer is not pickup")
    if envelope.message_kind is not MessageKind.INTEGRATION:
        raise ContractRejection("SCHEMA_MISMATCH", "message_kind is not integration")
    if envelope.message_kind.value != PICKUP_ACCEPTED_MESSAGE_KIND:
        raise ContractRejection("SCHEMA_MISMATCH", "message_kind does not match registry")
    if envelope.aggregate_scope is not AggregateScope.AGGREGATE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_scope must be aggregate")
    if envelope.aggregate_scope.value != PICKUP_ACCEPTED_AGGREGATE_SCOPE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_scope does not match registry")
    if envelope.aggregate_type != PICKUP_ACCEPTED_AGGREGATE_TYPE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_type must be pickup_task")
    if envelope.aggregate_id is None:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_id is required")
    if envelope.aggregate_version is None or envelope.aggregate_version < 1:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "aggregate_version must be a positive PickupTask version",
        )
    if envelope.schema_uri is not None and envelope.schema_uri != PICKUP_ACCEPTED_SCHEMA_URI:
        raise ContractRejection("SCHEMA_MISMATCH", "schema_uri does not match registry")

    payload = envelope.payload
    if not isinstance(payload, dict):
        raise ContractRejection("SCHEMA_MISMATCH", "payload must be an object")
    _reject_forbidden_payload_keys(payload)
    for field_name in REQUIRED_PAYLOAD_FIELDS:
        if field_name not in payload or payload[field_name] in (None, ""):
            raise ContractRejection(
                "SCHEMA_MISMATCH",
                f"missing required payload field {field_name}",
            )

    pickup_task_id = _parse_uuid(payload["pickup_task_id"], field="pickup_task_id")
    if pickup_task_id != envelope.aggregate_id:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "payload.pickup_task_id must equal envelope aggregate_id",
        )
    shipment_id = _parse_uuid(payload["shipment_id"], field="shipment_id")

    outcome_raw = str(payload["outcome"])
    if outcome_raw == "REJECTED":
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "REJECTED is not a pickup.fact.accepted outcome",
        )
    if outcome_raw not in ALLOWED_OUTCOMES:
        raise ContractRejection("SCHEMA_MISMATCH", f"unsupported outcome {outcome_raw}")
    outcome = _OUTCOME_MAP[outcome_raw]

    assigned_driver = _validate_driver_identity(
        payload["assigned_driver_user_id"],
        field="assigned_driver_user_id",
        event_id=envelope.event_id,
    )
    acting_driver = _validate_driver_identity(
        payload["acting_driver_user_id"],
        field="acting_driver_user_id",
        event_id=envelope.event_id,
    )
    if acting_driver != assigned_driver:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "acting_driver_user_id must equal assigned_driver_user_id",
        )

    scanned_identifier = str(payload["scanned_identifier"]).strip()
    if not scanned_identifier:
        raise ContractRejection("SCHEMA_MISMATCH", "scanned_identifier must not be empty")

    accepted_at = _parse_accepted_at(payload["accepted_at"])
    exception_evidence = _media_refs_to_evidence(envelope.media_refs)
    if outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION and not exception_evidence:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "ACCEPTED_WITH_EXCEPTION requires envelope media_refs",
        )

    return ValidatedPickupAcceptedFact(
        event_id=envelope.event_id,
        correlation_id=envelope.correlation_id,
        event_type=envelope.event_type,
        event_version=envelope.event_version,
        aggregate_type=envelope.aggregate_type or PICKUP_ACCEPTED_AGGREGATE_TYPE,
        aggregate_id=envelope.aggregate_id,
        aggregate_version=envelope.aggregate_version,
        pickup_task_id=pickup_task_id,
        shipment_id=shipment_id,
        outcome=outcome,
        accepted_at=accepted_at,
        assigned_driver_user_id=assigned_driver,
        acting_driver_user_id=acting_driver,
        scanned_identifier=scanned_identifier,
        exception_evidence=exception_evidence,
    )


def _reject_forbidden_payload_keys(payload: dict[str, Any]) -> None:
    present = FORBIDDEN_PAYLOAD_KEYS.intersection(payload)
    if present:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            f"payload contains forbidden fields: {', '.join(sorted(present))}",
        )


def _validate_driver_identity(value: object, *, field: str, event_id: UUID) -> str:
    if not isinstance(value, str):
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} must not be empty")
    lowered = normalized.lower()
    if lowered in PLACEHOLDER_DRIVER_IDENTITIES:
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} must not be a placeholder identity")
    if lowered == "pickup" or normalized == str(event_id):
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            f"{field} must not be producer or event_id",
        )
    return normalized


def _media_refs_to_evidence(media_refs: list[MediaRef] | None) -> tuple[EvidenceReference, ...]:
    if not media_refs:
        return ()
    refs: list[EvidenceReference] = []
    for media in media_refs:
        storage_uri = f"s3://{media.bucket}/{media.key}"
        refs.append(EvidenceReference.from_reference(storage_uri))
    return tuple(refs)


def _parse_uuid(value: object, *, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} is not a UUID") from exc


def _parse_accepted_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return parse_utc_datetime(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", "accepted_at is not RFC 3339 UTC") from exc
