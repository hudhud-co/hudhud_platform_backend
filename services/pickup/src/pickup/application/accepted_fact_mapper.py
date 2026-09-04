"""Build and validate pickup.fact.accepted v1 envelopes."""

from __future__ import annotations

from datetime import datetime
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

from pickup.domain.entities import PickupTask
from pickup.domain.value_objects import AcceptanceOutcome, EvidenceMediaRef
from pickup.infrastructure.contracts.registry import (
    load_accepted_fact_registry,
    validate_accepted_fact_envelope,
)


def build_accepted_fact_envelope(
    *,
    task: PickupTask,
    outcome: AcceptanceOutcome,
    scanned_identifier: str,
    accepted_at: datetime,
    aggregate_version: int,
    event_id: UUID,
    correlation_id: UUID,
    acting_driver_user_id: str,
    media_refs: tuple[EvidenceMediaRef, ...] = (),
    causation_id: UUID | None = None,
    tenant_id: UUID | None = None,
    traceparent: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Create a complete validated pickup.fact.accepted v1 envelope JSON dict + subject."""
    contract = load_accepted_fact_registry().contract
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
    envelope = EventEnvelope(
        event_id=event_id,
        event_type=contract.event_type,
        event_version=contract.event_version,
        occurred_at=accepted_at,
        producer=contract.producer,
        message_kind=MessageKind.INTEGRATION,
        aggregate_scope=AggregateScope.AGGREGATE,
        aggregate_type=contract.aggregate_type,
        aggregate_id=task.pickup_task_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=traceparent,
        tenant_id=tenant_id,
        data_classification=DataClassification.INTERNAL,
        pii_present=False,
        schema_uri=contract.schema_uri,
        payload={
            "pickup_task_id": str(task.pickup_task_id),
            "shipment_id": str(task.shipment_id),
            "outcome": outcome.value,
            "accepted_at": format_utc_datetime(accepted_at),
            "assigned_driver_user_id": task.assigned_driver_user_id,
            "acting_driver_user_id": acting_driver_user_id,
            "scanned_identifier": scanned_identifier.strip(),
        },
        metadata={"replay": False},
        media_refs=envelope_media,
    )
    payload_json = envelope_to_json_dict(envelope)
    validate_accepted_fact_envelope(payload_json)
    return payload_json, contract.subject
