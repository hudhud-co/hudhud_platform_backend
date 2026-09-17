"""Envelope builders for Hub-owned integration events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from hub.domain.entities import DropOff, ParcelPresence
from hub.domain.value_objects import ParcelDisposition
from hub.infrastructure.contracts.registry import (
    DROP_OFF_ACCEPTED_EVENT_TYPE,
    DROP_OFF_ACCEPTED_EVENT_VERSION,
    PARCEL_HELD_EVENT_TYPE,
    PARCEL_HELD_EVENT_VERSION,
    load_hub_fact_registry,
    validate_envelope,
)

ENVELOPE_VERSION = 1


def _rfc3339(value: datetime) -> str:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _base(
    *,
    event_type: str,
    event_version: int,
    event_id: UUID,
    occurred_at: datetime,
    aggregate_id: UUID,
    aggregate_version: int,
    correlation_id: UUID,
    causation_id: UUID | None,
    traceparent: str | None,
) -> tuple[dict[str, Any], str]:
    contract = load_hub_fact_registry(event_type, event_version).contract
    envelope: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "event_id": str(event_id),
        "event_type": contract.event_type,
        "event_version": contract.event_version,
        "occurred_at": _rfc3339(occurred_at),
        "producer": contract.producer,
        "message_kind": contract.message_kind,
        "aggregate_scope": contract.aggregate_scope,
        "aggregate_type": contract.aggregate_type,
        "aggregate_id": str(aggregate_id),
        "aggregate_version": aggregate_version,
        "correlation_id": str(correlation_id),
        "causation_id": str(causation_id) if causation_id else None,
        "data_classification": "confidential",
        # Hub facts carry parcel and facility identifiers. Hub never holds the receiver's
        # contact details, so none can leak from here.
        "pii_present": False,
        "schema_uri": contract.schema_uri,
    }
    if traceparent:
        envelope["traceparent"] = traceparent
    return envelope, contract.subject


def build_drop_off_accepted_envelope(
    *,
    drop_off: DropOff,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    destination_governorate: str | None = None,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if drop_off.label_code is None or drop_off.weight_grams is None:
        msg = "an accepted drop-off must carry the label and weight hub staff recorded"
        raise ValueError(msg)
    if drop_off.accepted_by_actor_id is None:
        msg = "an accepted drop-off must name the operator who accepted custody"
        raise ValueError(msg)
    accepted_at = drop_off.accepted_at or datetime.now(tz=UTC)

    envelope, subject = _base(
        event_type=DROP_OFF_ACCEPTED_EVENT_TYPE,
        event_version=DROP_OFF_ACCEPTED_EVENT_VERSION,
        event_id=event_id,
        occurred_at=accepted_at,
        aggregate_id=drop_off.drop_off_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "drop_off_id": str(drop_off.drop_off_id),
        "hub_id": str(drop_off.hub_id),
        "shipment_request_id": (
            str(drop_off.shipment_request_id)
            if drop_off.shipment_request_id is not None
            else None
        ),
        "tracking_code": drop_off.tracking_code,
        "label_code": drop_off.label_code,
        "weight_grams": drop_off.weight_grams,
        "destination_governorate": destination_governorate,
        "accepted_at": _rfc3339(accepted_at),
        "accepting_actor_id": str(drop_off.accepted_by_actor_id),
    }
    validate_envelope(
        envelope,
        event_type=DROP_OFF_ACCEPTED_EVENT_TYPE,
        event_version=DROP_OFF_ACCEPTED_EVENT_VERSION,
    )
    return envelope, subject


def build_parcel_held_envelope(
    *,
    presence: ParcelPresence,
    aggregate_version: int,
    held_at: datetime,
    event_id: UUID,
    correlation_id: UUID,
    reported_by_actor_id: UUID | None = None,
    causation_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    if presence.hold_reason is None:
        msg = "a held parcel must record why"
        raise ValueError(msg)
    disposition = presence.disposition or ParcelDisposition.AWAIT_OPERATIONS

    envelope, subject = _base(
        event_type=PARCEL_HELD_EVENT_TYPE,
        event_version=PARCEL_HELD_EVENT_VERSION,
        event_id=event_id,
        occurred_at=held_at,
        aggregate_id=presence.presence_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
    )
    envelope["payload"] = {
        "hub_id": str(presence.hub_id),
        "tracking_code": presence.tracking_code,
        "consignment_id": (
            str(presence.consignment_id) if presence.consignment_id is not None else None
        ),
        "reason": presence.hold_reason.value,
        "disposition": disposition.value,
        "held_at": _rfc3339(held_at),
        "reported_by_actor_id": (
            str(reported_by_actor_id) if reported_by_actor_id is not None else None
        ),
    }
    validate_envelope(
        envelope,
        event_type=PARCEL_HELD_EVENT_TYPE,
        event_version=PARCEL_HELD_EVENT_VERSION,
    )
    return envelope, subject
