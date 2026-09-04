"""Map between domain entities and persistence rows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus

from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AcceptanceIdempotencyRecord,
    AuditLogEntry,
    OrderIntent,
    PickupTaskSnapshot,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.types import InboxRow
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    EvidenceReference,
    PickupTaskAcceptanceState,
    PickupTaskStatus,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.infrastructure.persistence.models import (
    AcceptanceAuditLogRow,
    AcceptanceDecisionRow,
    AcceptanceIdempotencyRow,
    IntegrationInboxRow,
    OrderIntentRow,
    PickupTaskSnapshotRow,
    ShipmentEventRow,
    ShipmentRow,
)


def order_intent_to_row(order_intent: OrderIntent) -> OrderIntentRow:
    return OrderIntentRow(
        order_id=order_intent.order_id,
        shipment_id=order_intent.shipment_id,
        created_at=order_intent.created_at,
    )


def order_intent_from_row(row: OrderIntentRow) -> OrderIntent:
    return OrderIntent(
        order_id=row.order_id,
        shipment_id=row.shipment_id,
        created_at=row.created_at,
    )


def shipment_to_row(shipment: Shipment, *, version: int) -> ShipmentRow:
    return ShipmentRow(
        shipment_id=shipment.shipment_id,
        order_id=shipment.order_id,
        waybill_number=shipment.waybill_identity.waybill_number,
        current_status=shipment.current_status.value,
        order_created_at=shipment.order_created_at,
        accepted_at=shipment.accepted_at,
        sla_started_at=shipment.sla_started_at,
        current_custody_type=(
            shipment.current_custody_type.value
            if shipment.current_custody_type is not None
            else None
        ),
        current_custody_id=shipment.current_custody_id,
        version=version,
    )


def shipment_from_row(row: ShipmentRow) -> tuple[Shipment, int]:
    shipment = Shipment(
        shipment_id=row.shipment_id,
        order_id=row.order_id,
        waybill_identity=WaybillIdentity(
            waybill_number=row.waybill_number,
            shipment_id=str(row.shipment_id),
        ),
        current_status=ShipmentStatus(row.current_status),
        order_created_at=row.order_created_at,
        accepted_at=row.accepted_at,
        sla_started_at=row.sla_started_at,
        current_custody_type=(
            CustodyType(row.current_custody_type) if row.current_custody_type is not None else None
        ),
        current_custody_id=row.current_custody_id,
        version=row.version,
    )
    return shipment, row.version


def pickup_task_to_row(pickup_task: PickupTaskSnapshot, *, version: int) -> PickupTaskSnapshotRow:
    return PickupTaskSnapshotRow(
        pickup_task_id=pickup_task.pickup_task_id,
        shipment_id=pickup_task.shipment_id,
        status=pickup_task.status.value,
        assigned_driver_user_id=pickup_task.assigned_driver_user_id,
        assigned_batch_id=pickup_task.assigned_batch_id,
        has_pickup_condition_proof=pickup_task.has_pickup_condition_proof,
        acceptance_state=(
            pickup_task.acceptance_state.value if pickup_task.acceptance_state is not None else None
        ),
        version=version,
    )


def pickup_task_from_row(row: PickupTaskSnapshotRow) -> tuple[PickupTaskSnapshot, int]:
    pickup_task = PickupTaskSnapshot(
        pickup_task_id=row.pickup_task_id,
        shipment_id=row.shipment_id,
        status=PickupTaskStatus(row.status),
        assigned_driver_user_id=row.assigned_driver_user_id,
        assigned_batch_id=row.assigned_batch_id,
        has_pickup_condition_proof=row.has_pickup_condition_proof,
        acceptance_state=(
            PickupTaskAcceptanceState(row.acceptance_state)
            if row.acceptance_state is not None
            else None
        ),
    )
    return pickup_task, row.version


def shipment_event_to_row(event: ShipmentEvent) -> ShipmentEventRow:
    return ShipmentEventRow(
        event_id=event.event_id,
        shipment_id=event.shipment_id,
        event_type=event.event_type.value,
        previous_status=event.previous_status.value,
        new_status=event.new_status.value,
        occurred_at=event.occurred_at,
    )


def shipment_event_from_row(row: ShipmentEventRow) -> ShipmentEvent:
    return ShipmentEvent(
        event_id=row.event_id,
        shipment_id=row.shipment_id,
        event_type=ShipmentEventType(row.event_type),
        previous_status=ShipmentStatus(row.previous_status),
        new_status=ShipmentStatus(row.new_status),
        occurred_at=row.occurred_at,
    )


def audit_log_to_row(entry: AuditLogEntry) -> AcceptanceAuditLogRow:
    return AcceptanceAuditLogRow(
        audit_id=entry.audit_id,
        action=entry.action,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        actor_id=entry.actor_id,
        occurred_at=entry.occurred_at,
        details=dict(entry.details),
    )


def audit_log_from_row(row: AcceptanceAuditLogRow) -> AuditLogEntry:
    return AuditLogEntry(
        audit_id=row.audit_id,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        actor_id=row.actor_id,
        occurred_at=row.occurred_at,
        details=dict(row.details),
    )


def acceptance_decision_from_audit(entry: AuditLogEntry) -> AcceptanceDecisionRow:
    details = entry.details
    exception_evidence = _parse_exception_evidence(details.get("exception_evidence_uris", ""))
    scan_timestamp_raw = details.get("scan_timestamp")
    if scan_timestamp_raw is None:
        scan_timestamp = entry.occurred_at
    else:
        scan_timestamp = datetime.fromisoformat(scan_timestamp_raw)
    return AcceptanceDecisionRow(
        decision_id=uuid4(),
        shipment_id=UUID(entry.entity_id),
        pickup_task_id=UUID(details["pickup_task_id"]),
        outcome=details["outcome"],
        acting_driver_user_id=entry.actor_id,
        scanned_identifier=details["scanned_identifier"],
        scan_timestamp=scan_timestamp,
        recorded_at=entry.occurred_at,
        exception_evidence=exception_evidence,
    )


def _parse_exception_evidence(raw: str) -> list[dict[str, str | bool | None]]:
    if not raw:
        return []
    return [{"storage_uri": uri.strip()} for uri in raw.split(",") if uri.strip()]


def acceptance_idempotency_to_row(record: AcceptanceIdempotencyRecord) -> AcceptanceIdempotencyRow:
    return AcceptanceIdempotencyRow(
        idempotency_key=record.idempotency_key,
        command_fingerprint=record.command_fingerprint,
        shipment_id=record.shipment_id,
        pickup_task_id=record.pickup_task_id,
        recorded_at=record.recorded_at,
    )


def acceptance_idempotency_from_row(row: AcceptanceIdempotencyRow) -> AcceptanceIdempotencyRecord:
    return AcceptanceIdempotencyRecord(
        idempotency_key=row.idempotency_key,
        command_fingerprint=row.command_fingerprint,
        shipment_id=row.shipment_id,
        pickup_task_id=row.pickup_task_id,
        recorded_at=row.recorded_at,
    )


def decision_to_row(decision: AcceptanceDecisionRecord) -> AcceptanceDecisionRow:
    return AcceptanceDecisionRow(
        decision_id=decision.decision_id,
        shipment_id=decision.shipment_id,
        pickup_task_id=decision.pickup_task_id,
        outcome=decision.outcome.value,
        acting_driver_user_id=decision.acting_driver_user_id,
        scanned_identifier=decision.scanned_identifier,
        scan_timestamp=decision.scan_timestamp,
        recorded_at=decision.recorded_at,
        exception_evidence=_evidence_to_json(decision.exception_evidence),
    )


def decision_from_row(row: AcceptanceDecisionRow) -> AcceptanceDecisionRecord:
    return AcceptanceDecisionRecord(
        decision_id=row.decision_id,  # type: ignore[arg-type]
        shipment_id=row.shipment_id,  # type: ignore[arg-type]
        pickup_task_id=row.pickup_task_id,  # type: ignore[arg-type]
        outcome=AcceptanceOutcome(row.outcome),
        acting_driver_user_id=row.acting_driver_user_id,
        scanned_identifier=row.scanned_identifier,
        scan_timestamp=row.scan_timestamp,  # type: ignore[arg-type]
        recorded_at=row.recorded_at,  # type: ignore[arg-type]
        exception_evidence=_evidence_from_json(row.exception_evidence),
    )


def inbox_from_row(row: IntegrationInboxRow) -> InboxRow:
    return InboxRow(
        id=row.id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        processing_owner=row.processing_owner,
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        handler_version=row.handler_version,
        attempt_count=row.attempt_count,
        first_received_at=row.first_received_at,  # type: ignore[arg-type]
        last_received_at=row.last_received_at,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        quarantined_at=row.quarantined_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        jetstream_stream=row.jetstream_stream,
        jetstream_seq=row.jetstream_seq,
        correlation_id=row.correlation_id,  # type: ignore[arg-type]
        nats_msg_id=row.nats_msg_id,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,  # type: ignore[arg-type]
        aggregate_version=row.aggregate_version,
    )


def _evidence_to_json(
    evidence: tuple[EvidenceReference, ...],
) -> list[dict[str, str | bool | None]]:
    return [{"storage_uri": item.storage_uri} for item in evidence]


def _evidence_from_json(
    raw: list[dict[str, str | bool | None]] | None,
) -> tuple[EvidenceReference, ...]:
    if not raw:
        return ()
    refs: list[EvidenceReference] = []
    for item in raw:
        storage_uri = item.get("storage_uri")
        if not isinstance(storage_uri, str) or not storage_uri.strip():
            continue
        refs.append(EvidenceReference.from_reference(storage_uri))
    return tuple(refs)
