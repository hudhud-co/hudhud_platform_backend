"""Build and validate pickup.fact.handover_completed v1 envelopes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from event_envelope import (
    AggregateScope,
    DataClassification,
    EventEnvelope,
    MediaRef,
    MessageKind,
    envelope_to_json_dict,
)
from event_envelope.primitives import format_utc_datetime

from pickup.domain.handover import HandoverDiscrepancyReason
from pickup.domain.value_objects import EvidenceMediaRef
from pickup.infrastructure.contracts.registry import (
    load_handover_completed_registry,
    validate_handover_completed_envelope,
)


class HandoverOutcome(StrEnum):
    """Custody-releasing hub receipt outcomes. MISSING never releases custody."""

    RECEIVED = "RECEIVED"
    RECEIVED_WITH_DISCREPANCY = "RECEIVED_WITH_DISCREPANCY"


#: Discrepancy reasons that still release custody to the hub.
RELEASING_DISCREPANCY_REASONS: frozenset[HandoverDiscrepancyReason] = frozenset(
    {
        HandoverDiscrepancyReason.DAMAGED_AT_HANDOVER,
        HandoverDiscrepancyReason.WRONG_HUB_RECEIVED,
        HandoverDiscrepancyReason.OTHER,
    }
)


def build_handover_completed_envelope(
    *,
    pickup_task_id: UUID,
    shipment_id: UUID,
    handover_manifest_id: UUID,
    receiving_hub_id: UUID,
    outcome: HandoverOutcome,
    released_at: datetime,
    releasing_driver_user_id: str,
    receiving_actor_id: str,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    discrepancy_reason: HandoverDiscrepancyReason | None = None,
    media_refs: tuple[EvidenceMediaRef, ...] = (),
    causation_id: UUID | None = None,
    tenant_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Create a validated pickup.fact.handover_completed v1 envelope and its subject."""
    if receiving_actor_id == releasing_driver_user_id:
        # JSON Schema cannot compare two payload fields; enforce it here.
        msg = "receiving actor must differ from the releasing driver"
        raise ValueError(msg)
    if outcome is HandoverOutcome.RECEIVED_WITH_DISCREPANCY:
        if discrepancy_reason is None:
            msg = "RECEIVED_WITH_DISCREPANCY requires a discrepancy reason"
            raise ValueError(msg)
        if discrepancy_reason not in RELEASING_DISCREPANCY_REASONS:
            msg = f"{discrepancy_reason.value} does not release custody"
            raise ValueError(msg)
        if not media_refs:
            msg = "RECEIVED_WITH_DISCREPANCY requires at least one media reference"
            raise ValueError(msg)

    contract = load_handover_completed_registry().contract
    envelope_media = (
        [
            MediaRef(
                ref_type=ref.ref_type,
                bucket=ref.bucket,
                key=ref.key,
                content_type=ref.content_type,
            )
            for ref in media_refs
        ]
        if media_refs
        else None
    )
    payload: dict[str, Any] = {
        "pickup_task_id": str(pickup_task_id),
        "shipment_id": str(shipment_id),
        "handover_manifest_id": str(handover_manifest_id),
        "receiving_hub_id": str(receiving_hub_id),
        "outcome": outcome.value,
        "released_at": format_utc_datetime(released_at),
        "releasing_driver_user_id": releasing_driver_user_id,
        "receiving_actor_id": receiving_actor_id,
    }
    if discrepancy_reason is not None:
        payload["discrepancy_reason"] = discrepancy_reason.value

    envelope = EventEnvelope(
        event_id=event_id,
        event_type=contract.event_type,
        event_version=contract.event_version,
        occurred_at=released_at,
        producer=contract.producer,
        message_kind=MessageKind.INTEGRATION,
        aggregate_scope=AggregateScope.AGGREGATE,
        aggregate_type=contract.aggregate_type,
        aggregate_id=pickup_task_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
        tenant_id=tenant_id,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        schema_uri=contract.schema_uri,
        payload=payload,
        metadata={"replay": False},
        media_refs=envelope_media,
    )
    payload_json = envelope_to_json_dict(envelope)
    validate_handover_completed_envelope(payload_json)
    return payload_json, contract.subject
