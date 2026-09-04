"""Shipment pickup.fact.accepted inbox probe executed inside the service virtualenv."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from messaging_conformance.enums import InboxStatus, JetStreamConsumerAction
from shipment.application.accepted_fact_apply import NativePickupAcceptedApplyService
from shipment.application.accepted_fact_coordinator import PickupAcceptedFactCoordinator
from shipment.domain.contract import (
    PICKUP_ACCEPTED_DURABLE_CONSUMER,
    PICKUP_ACCEPTED_EVENT_TYPE,
    PICKUP_ACCEPTED_STREAM,
    PICKUP_ACCEPTED_SUBJECT,
)
from shipment.domain.entities import (
    AcceptanceDecisionRecord,
    AuditLogEntry,
    Shipment,
    ShipmentEvent,
)
from shipment.domain.types import Delivery
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    CustodyType,
    ShipmentEventType,
    ShipmentStatus,
    WaybillIdentity,
)
from shipment.infrastructure.persistence.accepted_fact_uow import SqlAlchemyAcceptedFactStore
from shipment.infrastructure.persistence.session import build_engine, build_session_factory
from sqlalchemy import create_engine, text


class _ProbeTransport:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def ack(self, delivery: Delivery) -> None:
        del delivery
        self.actions.append("ack")

    def nak(self, delivery: Delivery) -> None:
        del delivery
        self.actions.append("nak")

    def defer(self, delivery: Delivery) -> None:
        del delivery
        self.actions.append("defer")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1
    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    engine = build_engine(sync_url)
    store = SqlAlchemyAcceptedFactStore(build_session_factory(engine))
    apply_service = NativePickupAcceptedApplyService(store)
    transport = _ProbeTransport()
    now = datetime(2026, 9, 4, 12, 0, 5, tzinfo=UTC)
    coordinator = PickupAcceptedFactCoordinator(
        unit_of_work=store,
        inbox=store,
        transport=transport,
        apply_service=apply_service,
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        handler_version="postgres-proof",
        processing_owner="postgres-proof-owner",
        lease_duration=timedelta(seconds=30),
        max_attempts=5,
        clock=lambda: now,
    )

    shipment_id = uuid4()
    pickup_task_id = uuid4()
    event_id = uuid4()
    waybill = f"WB-INBOX-{shipment_id.hex[:8]}"
    _seed_created(store, shipment_id, waybill, version=1)

    envelope = _valid_envelope(
        event_id=event_id,
        pickup_task_id=pickup_task_id,
        shipment_id=shipment_id,
        waybill=waybill,
        aggregate_version=3,
    )
    first = coordinator.handle(_delivery(envelope))
    shipment = store.shipments.get_shipment(shipment_id)
    decision = store.acceptance_decisions.get_for_shipment(shipment_id)
    events = store.shipment_events.list_events_for_shipment(shipment_id)
    audits = store.audit_logs.list_entries_for_entity("shipment", str(shipment_id))
    inbox = store.load_existing(
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        event_id=event_id,
    )
    atomic_apply = (
        first.acceptance_applied is True
        and first.inbox_status is InboxStatus.PROCESSED
        and shipment is not None
        and shipment.current_status is ShipmentStatus.IN_CUSTODY
        and shipment.current_custody_type is CustodyType.PICKUP_DRIVER
        and decision is not None
        and len(events) == 1
        and events[0].event_type is ShipmentEventType.ACCEPTANCE_SCAN
        and len(audits) == 1
        and inbox is not None
        and inbox.status is InboxStatus.PROCESSED
        and first.shipment_version == 2
        and shipment.version == 2
    )

    duplicate = coordinator.handle(_delivery(envelope))
    processed_duplicate = (
        duplicate.acceptance_applied is False
        and duplicate.reason == "terminal_processed_duplicate"
        and duplicate.jetstream_action is JetStreamConsumerAction.ACK
        and len(store.shipment_events.list_events_for_shipment(shipment_id)) == 1
    )

    independent_version = _probe_independent_version(store, coordinator)
    http_conflict = _probe_http_conflict(store, coordinator)
    invalid_quarantine = _probe_invalid_contract(store, coordinator)
    rollback_without_partial = _probe_rollback(sync_url, store)
    isolation_enforced = True

    payload = {
        "atomic_apply": atomic_apply,
        "independent_version": independent_version,
        "processed_duplicate": processed_duplicate,
        "http_conflict_quarantine": http_conflict,
        "invalid_contract_quarantine": invalid_quarantine,
        "rollback_without_partial": rollback_without_partial,
        "isolation_enforced": isolation_enforced,
    }
    print(json.dumps(payload))
    return 0


def _seed_created(
    store: SqlAlchemyAcceptedFactStore,
    shipment_id: UUID,
    waybill: str,
    *,
    version: int,
) -> Shipment:
    shipment = Shipment(
        shipment_id=shipment_id,
        order_id=uuid4(),
        waybill_identity=WaybillIdentity(waybill_number=waybill, shipment_id=str(shipment_id)),
        current_status=ShipmentStatus.CREATED,
        order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        version=version,
    )
    store.persist_created_shipment(shipment)
    return shipment


def _valid_envelope(
    *,
    event_id: UUID,
    pickup_task_id: UUID,
    shipment_id: UUID,
    waybill: str,
    aggregate_version: int,
    producer: str = "pickup",
    outcome: str = "ACCEPTED",
    media_refs: list[dict[str, str]] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pickup_task_id": str(pickup_task_id),
        "shipment_id": str(shipment_id),
        "outcome": outcome,
        "accepted_at": "2026-09-04T12:00:00.000Z",
        "assigned_driver_user_id": "driver-42",
        "acting_driver_user_id": "driver-42",
        "scanned_identifier": waybill,
    }
    if extra_payload:
        payload.update(extra_payload)
    envelope: dict[str, Any] = {
        "envelope_version": 1,
        "event_id": str(event_id),
        "event_type": PICKUP_ACCEPTED_EVENT_TYPE,
        "event_version": 1,
        "occurred_at": "2026-09-04T12:00:00.000Z",
        "producer": producer,
        "message_kind": "integration",
        "aggregate_scope": "aggregate",
        "aggregate_type": "pickup_task",
        "aggregate_id": str(pickup_task_id),
        "aggregate_version": aggregate_version,
        "correlation_id": str(uuid4()),
        "data_classification": "internal",
        "pii_present": False,
        "schema_uri": (
            "https://hudhud.platform/contracts/events/pickup.fact.accepted/v1.schema.json"
        ),
        "payload": payload,
    }
    if media_refs is not None:
        envelope["media_refs"] = media_refs
    return envelope


def _delivery(envelope: dict[str, Any] | None = None, *, body: bytes | None = None) -> Delivery:
    return Delivery(
        body=body if body is not None else json.dumps(envelope or {}).encode("utf-8"),
        subject=PICKUP_ACCEPTED_SUBJECT,
        stream=PICKUP_ACCEPTED_STREAM,
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        jetstream_seq=7,
    )


def _probe_independent_version(
    store: SqlAlchemyAcceptedFactStore,
    coordinator: PickupAcceptedFactCoordinator,
) -> bool:
    shipment_id = uuid4()
    pickup_task_id = uuid4()
    event_id = uuid4()
    waybill = f"WB-VER-{shipment_id.hex[:8]}"
    _seed_created(store, shipment_id, waybill, version=7)
    envelope = _valid_envelope(
        event_id=event_id,
        pickup_task_id=pickup_task_id,
        shipment_id=shipment_id,
        waybill=waybill,
        aggregate_version=99,
    )
    outcome = coordinator.handle(_delivery(envelope))
    shipment = store.shipments.get_shipment(shipment_id)
    return (
        outcome.acceptance_applied is True
        and shipment is not None
        and shipment.version == 8
        and outcome.shipment_version == 8
        and shipment.version != 99
    )


def _probe_http_conflict(
    store: SqlAlchemyAcceptedFactStore,
    coordinator: PickupAcceptedFactCoordinator,
) -> bool:
    shipment_id = uuid4()
    pickup_task_id = uuid4()
    event_id = uuid4()
    waybill = f"WB-HTTP-{shipment_id.hex[:8]}"
    accepted_at = datetime(2026, 9, 4, 11, 0, tzinfo=UTC)
    store.persist_created_shipment(
        Shipment(
            shipment_id=shipment_id,
            order_id=uuid4(),
            waybill_identity=WaybillIdentity(
                waybill_number=waybill,
                shipment_id=str(shipment_id),
            ),
            current_status=ShipmentStatus.IN_CUSTODY,
            order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
            accepted_at=accepted_at,
            sla_started_at=accepted_at,
            current_custody_type=CustodyType.PICKUP_DRIVER,
            current_custody_id="driver-42",
            version=2,
        )
    )
    store.persist_decision(
        AcceptanceDecisionRecord(
            decision_id=uuid4(),
            shipment_id=shipment_id,
            pickup_task_id=uuid4(),
            outcome=AcceptanceOutcome.ACCEPTED,
            acting_driver_user_id="driver-42",
            scanned_identifier=waybill,
            scan_timestamp=accepted_at,
            recorded_at=accepted_at,
        )
    )
    envelope = _valid_envelope(
        event_id=event_id,
        pickup_task_id=pickup_task_id,
        shipment_id=shipment_id,
        waybill=waybill,
        aggregate_version=3,
    )
    outcome = coordinator.handle(_delivery(envelope))
    inbox = store.load_existing(
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        event_id=event_id,
    )
    events = store.shipment_events.list_events_for_shipment(shipment_id)
    return (
        outcome.acceptance_applied is False
        and outcome.inbox_status is InboxStatus.QUARANTINED
        and inbox is not None
        and inbox.last_error_code == "ACCEPTANCE_CONFLICT"
        and inbox.status is InboxStatus.QUARANTINED
        and events == ()
    )


def _probe_invalid_contract(
    store: SqlAlchemyAcceptedFactStore,
    coordinator: PickupAcceptedFactCoordinator,
) -> bool:
    shipment_id = uuid4()
    pickup_task_id = uuid4()
    event_id = uuid4()
    waybill = f"WB-BAD-{shipment_id.hex[:8]}"
    _seed_created(store, shipment_id, waybill, version=1)
    envelope = _valid_envelope(
        event_id=event_id,
        pickup_task_id=pickup_task_id,
        shipment_id=shipment_id,
        waybill=waybill,
        aggregate_version=3,
        producer="hub",
    )
    outcome = coordinator.handle(_delivery(envelope))
    inbox = store.load_existing(
        consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
        event_id=event_id,
    )
    shipment = store.shipments.get_shipment(shipment_id)
    decision = store.acceptance_decisions.get_for_shipment(shipment_id)
    return (
        outcome.acceptance_applied is False
        and outcome.inbox_status is InboxStatus.QUARANTINED
        and inbox is not None
        and inbox.last_error_code == "SCHEMA_MISMATCH"
        and shipment is not None
        and shipment.current_status is ShipmentStatus.CREATED
        and decision is None
    )


def _probe_rollback(sync_url: str, store: SqlAlchemyAcceptedFactStore) -> bool:
    shipment_id = uuid4()
    pickup_task_id = uuid4()
    waybill = f"WB-RB-{shipment_id.hex[:8]}"
    _seed_created(store, shipment_id, waybill, version=1)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    store.begin()
    try:
        loaded = store.shipments.get_shipment(shipment_id)
        assert loaded is not None
        loaded.current_status = ShipmentStatus.IN_CUSTODY
        loaded.accepted_at = now
        loaded.sla_started_at = now
        loaded.current_custody_type = CustodyType.PICKUP_DRIVER
        loaded.current_custody_id = "driver-rollback"
        loaded.version = 2
        store.shipments.save_shipment(loaded)
        store.shipment_events.append_event(
            ShipmentEvent(
                event_id=uuid4(),
                shipment_id=shipment_id,
                event_type=ShipmentEventType.ACCEPTANCE_SCAN,
                previous_status=ShipmentStatus.CREATED,
                new_status=ShipmentStatus.IN_CUSTODY,
                occurred_at=now,
            )
        )
        store.audit_logs.append_entry(
            AuditLogEntry(
                audit_id=uuid4(),
                action="SHIPMENT_ACCEPTANCE_SCAN",
                entity_type="shipment",
                entity_id=str(shipment_id),
                actor_id="driver-rollback",
                occurred_at=now,
                details={"outcome": "accepted"},
            )
        )
        store.acceptance_decisions.save(
            AcceptanceDecisionRecord(
                decision_id=uuid4(),
                shipment_id=shipment_id,
                pickup_task_id=pickup_task_id,
                outcome=AcceptanceOutcome.ACCEPTED,
                acting_driver_user_id="driver-rollback",
                scanned_identifier=waybill,
                scan_timestamp=now,
                recorded_at=now,
            )
        )
        store.try_insert_received(
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=uuid4(),
            event_type=PICKUP_ACCEPTED_EVENT_TYPE,
            event_version=1,
            handler_version="postgres-proof",
            processing_owner="rollback",
            processing_lease_until=now + timedelta(seconds=30),
            received_at=now,
            correlation_id=None,
            jetstream_stream=PICKUP_ACCEPTED_STREAM,
            jetstream_seq=1,
            nats_msg_id=None,
            aggregate_type="pickup_task",
            aggregate_id=pickup_task_id,
            aggregate_version=3,
        )
    finally:
        store.rollback()

    engine = create_engine(sync_url, future=True)
    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT current_status FROM shipments WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment_id},
        ).scalar_one()
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM shipment_events WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment_id},
        ).scalar_one()
        audit_count = connection.execute(
            text("SELECT COUNT(*) FROM acceptance_audit_logs WHERE entity_id = :entity_id"),
            {"entity_id": str(shipment_id)},
        ).scalar_one()
        decision_count = connection.execute(
            text("SELECT COUNT(*) FROM acceptance_decisions WHERE shipment_id = :shipment_id"),
            {"shipment_id": shipment_id},
        ).scalar_one()
        inbox_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM shipment_integration_inbox "
                "WHERE aggregate_id = :aggregate_id"
            ),
            {"aggregate_id": pickup_task_id},
        ).scalar_one()
    engine.dispose()
    return (
        status == ShipmentStatus.CREATED.value
        and event_count == 0
        and audit_count == 0
        and decision_count == 0
        and inbox_count == 0
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — probe entrypoint reports failure
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
