"""pickup.fact.handover_completed envelope and payload validation for Shipment.

Two invariants JSON Schema cannot express are enforced here, because both would
otherwise let a driver release its own custody:

* ``receiving_actor_id`` must differ from ``releasing_driver_user_id``;
* ``MISSING_FROM_DRIVER`` is never a custody-releasing discrepancy reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from event_envelope.enums import AggregateScope, MessageKind
from event_envelope.envelope import EventEnvelope
from event_envelope.media_refs import MediaRef
from event_envelope.primitives import parse_utc_datetime

from shipment.domain.contract import (
    ALLOWED_HANDOVER_DISCREPANCY_REASONS,
    ALLOWED_HANDOVER_OUTCOMES,
    FORBIDDEN_PAYLOAD_KEYS,
    PICKUP_HANDOVER_AGGREGATE_SCOPE,
    PICKUP_HANDOVER_AGGREGATE_TYPE,
    PICKUP_HANDOVER_DURABLE_CONSUMER,
    PICKUP_HANDOVER_EVENT_TYPE,
    PICKUP_HANDOVER_EVENT_VERSION,
    PICKUP_HANDOVER_MESSAGE_KIND,
    PICKUP_HANDOVER_PRODUCER,
    PICKUP_HANDOVER_SCHEMA_URI,
    PICKUP_HANDOVER_STREAM,
    PICKUP_HANDOVER_SUBJECT,
    PLACEHOLDER_DRIVER_IDENTITIES,
    REQUIRED_HANDOVER_PAYLOAD_FIELDS,
)
from shipment.domain.errors import ContractRejection
from shipment.domain.types import Delivery, ValidatedPickupHandoverFact
from shipment.domain.value_objects import EvidenceReference, HandoverOutcome

_OUTCOME_MAP: dict[str, HandoverOutcome] = {
    "RECEIVED": HandoverOutcome.RECEIVED,
    "RECEIVED_WITH_DISCREPANCY": HandoverOutcome.RECEIVED_WITH_DISCREPANCY,
}


def validate_pickup_handover_delivery(
    *,
    envelope: EventEnvelope,
    delivery: Delivery,
) -> ValidatedPickupHandoverFact:
    """Fail closed on contract/envelope mismatches before any custody work."""
    _validate_transport(delivery)
    _validate_envelope(envelope)

    payload = envelope.payload
    if not isinstance(payload, dict):
        raise ContractRejection("SCHEMA_MISMATCH", "payload must be an object")
    _reject_forbidden_payload_keys(payload)
    for field_name in REQUIRED_HANDOVER_PAYLOAD_FIELDS:
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

    outcome_raw = str(payload["outcome"])
    if outcome_raw == "MISSING":
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "a missing parcel does not release custody and must not use this event type",
        )
    if outcome_raw not in ALLOWED_HANDOVER_OUTCOMES:
        raise ContractRejection("SCHEMA_MISMATCH", f"unsupported outcome {outcome_raw}")
    outcome = _OUTCOME_MAP[outcome_raw]

    discrepancy_reason = _validate_discrepancy(payload, outcome)

    releasing_driver = _validate_actor_identity(
        payload["releasing_driver_user_id"],
        field="releasing_driver_user_id",
        event_id=envelope.event_id,
    )
    receiving_actor = _validate_actor_identity(
        payload["receiving_actor_id"],
        field="receiving_actor_id",
        event_id=envelope.event_id,
    )
    if receiving_actor == releasing_driver:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "receiving_actor_id must differ from releasing_driver_user_id",
        )

    condition_evidence = _media_refs_to_evidence(envelope.media_refs)
    if outcome is HandoverOutcome.RECEIVED_WITH_DISCREPANCY and not condition_evidence:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "RECEIVED_WITH_DISCREPANCY requires envelope media_refs",
        )

    return ValidatedPickupHandoverFact(
        event_id=envelope.event_id,
        correlation_id=envelope.correlation_id,
        event_type=envelope.event_type,
        event_version=envelope.event_version,
        aggregate_type=envelope.aggregate_type or PICKUP_HANDOVER_AGGREGATE_TYPE,
        aggregate_id=envelope.aggregate_id,
        aggregate_version=envelope.aggregate_version,
        pickup_task_id=pickup_task_id,
        shipment_id=_parse_uuid(payload["shipment_id"], field="shipment_id"),
        handover_manifest_id=_parse_uuid(
            payload["handover_manifest_id"], field="handover_manifest_id"
        ),
        receiving_hub_id=_parse_uuid(payload["receiving_hub_id"], field="receiving_hub_id"),
        outcome=outcome,
        discrepancy_reason=discrepancy_reason,
        released_at=_parse_released_at(payload["released_at"]),
        releasing_driver_user_id=releasing_driver,
        receiving_actor_id=receiving_actor,
        condition_evidence=condition_evidence,
    )


def _validate_transport(delivery: Delivery) -> None:
    if delivery.subject != PICKUP_HANDOVER_SUBJECT:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "delivered subject is not pickup.fact.handover_completed",
        )
    if delivery.stream and delivery.stream != PICKUP_HANDOVER_STREAM:
        raise ContractRejection("SUBJECT_FORBIDDEN", "delivered stream is not HUDHUD_PICKUP")
    if delivery.consumer_name and delivery.consumer_name != PICKUP_HANDOVER_DURABLE_CONSUMER:
        raise ContractRejection(
            "SUBJECT_FORBIDDEN",
            "durable consumer is not shipment_pickup_handover_facts_v1",
        )


def _validate_envelope(envelope: EventEnvelope) -> None:
    if envelope.event_type != PICKUP_HANDOVER_EVENT_TYPE:
        raise ContractRejection(
            "SCHEMA_MISMATCH", "event_type is not pickup.fact.handover_completed"
        )
    if envelope.event_version != PICKUP_HANDOVER_EVENT_VERSION:
        raise ContractRejection("SCHEMA_MISMATCH", "event_version is not 1")
    if envelope.producer != PICKUP_HANDOVER_PRODUCER:
        raise ContractRejection("SCHEMA_MISMATCH", "producer is not pickup")
    if envelope.message_kind is not MessageKind.INTEGRATION:
        raise ContractRejection("SCHEMA_MISMATCH", "message_kind is not integration")
    if envelope.message_kind.value != PICKUP_HANDOVER_MESSAGE_KIND:
        raise ContractRejection("SCHEMA_MISMATCH", "message_kind does not match registry")
    if envelope.aggregate_scope is not AggregateScope.AGGREGATE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_scope must be aggregate")
    if envelope.aggregate_scope.value != PICKUP_HANDOVER_AGGREGATE_SCOPE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_scope does not match registry")
    if envelope.aggregate_type != PICKUP_HANDOVER_AGGREGATE_TYPE:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_type must be pickup_task")
    if envelope.aggregate_id is None:
        raise ContractRejection("SCHEMA_MISMATCH", "aggregate_id is required")
    if envelope.aggregate_version is None or envelope.aggregate_version < 1:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "aggregate_version must be a positive PickupTask version",
        )
    if envelope.schema_uri is not None and envelope.schema_uri != PICKUP_HANDOVER_SCHEMA_URI:
        raise ContractRejection("SCHEMA_MISMATCH", "schema_uri does not match registry")


def _validate_discrepancy(
    payload: dict[str, Any], outcome: HandoverOutcome
) -> str | None:
    raw = payload.get("discrepancy_reason")
    if outcome is HandoverOutcome.RECEIVED:
        if raw not in (None, ""):
            raise ContractRejection(
                "SCHEMA_MISMATCH",
                "a clean receipt must not carry a discrepancy reason",
            )
        return None
    reason = str(raw or "")
    if reason == "MISSING_FROM_DRIVER":
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            "MISSING_FROM_DRIVER does not release custody",
        )
    if reason not in ALLOWED_HANDOVER_DISCREPANCY_REASONS:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            f"unsupported discrepancy reason {reason}",
        )
    return reason


def _reject_forbidden_payload_keys(payload: dict[str, Any]) -> None:
    present = FORBIDDEN_PAYLOAD_KEYS.intersection(payload)
    if present:
        raise ContractRejection(
            "SCHEMA_MISMATCH",
            f"payload contains forbidden fields: {', '.join(sorted(present))}",
        )


def _validate_actor_identity(value: object, *, field: str, event_id: UUID) -> str:
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
    return tuple(
        EvidenceReference.from_reference(f"s3://{media.bucket}/{media.key}")
        for media in media_refs
    )


def _parse_uuid(value: object, *, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", f"{field} is not a UUID") from exc


def _parse_released_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return parse_utc_datetime(str(value))
    except (TypeError, ValueError) as exc:
        raise ContractRejection("SCHEMA_MISMATCH", "released_at is not RFC 3339 UTC") from exc
