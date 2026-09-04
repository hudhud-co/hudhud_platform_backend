"""Inspect Shipment canonical state after native accepted-fact apply."""

from __future__ import annotations

import json
import os
import sys
from uuid import UUID

from shipment.domain.contract import PICKUP_ACCEPTED_DURABLE_CONSUMER
from shipment.domain.value_objects import CustodyType, ShipmentStatus
from shipment.infrastructure.persistence.accepted_fact_uow import SqlAlchemyAcceptedFactStore
from shipment.infrastructure.persistence.session import build_engine, build_session_factory


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    shipment_id_raw = os.environ.get("SHIPMENT_ID")
    event_id_raw = os.environ.get("EVENT_ID")
    expected_custody_id = os.environ.get("EXPECTED_CUSTODY_ID")
    if not database_url or not shipment_id_raw or not event_id_raw:
        print("required probe inputs missing", file=sys.stderr)
        return 1

    shipment_id = UUID(shipment_id_raw)
    event_id = UUID(event_id_raw)
    engine = build_engine(database_url)
    try:
        store = SqlAlchemyAcceptedFactStore(build_session_factory(engine))
        shipment = store.shipments.get_shipment(shipment_id)
        events = store.shipment_events.list_events_for_shipment(shipment_id)
        audits = store.audit_logs.list_entries_for_entity("shipment", str(shipment_id))
        decision = store.acceptance_decisions.get_for_shipment(shipment_id)
        inbox = store.load_existing(
            consumer_name=PICKUP_ACCEPTED_DURABLE_CONSUMER,
            event_id=event_id,
        )
        status = shipment.current_status.value if shipment is not None else None
        custody_type = (
            shipment.current_custody_type.value
            if shipment is not None and shipment.current_custody_type is not None
            else None
        )
        print(
            json.dumps(
                {
                    "status": status,
                    "is_in_custody": status == ShipmentStatus.IN_CUSTODY.value,
                    "custody_type_pickup_driver": custody_type == CustodyType.PICKUP_DRIVER.value,
                    "custody_id_matches": (
                        shipment is not None
                        and expected_custody_id is not None
                        and shipment.current_custody_id == expected_custody_id
                    ),
                    "has_accepted_at": shipment is not None and shipment.accepted_at is not None,
                    "has_sla_started_at": bool(shipment is not None and shipment.sla_started_at),
                    "event_count": len(events),
                    "audit_count": len(audits),
                    "has_decision": decision is not None,
                    "decision_outcome": decision.outcome.value if decision is not None else None,
                    "exception_evidence_count": (
                        len(decision.exception_evidence) if decision is not None else 0
                    ),
                    "inbox_status": inbox.status.value if inbox is not None else None,
                    "inbox_attempt_count": inbox.attempt_count if inbox is not None else 0,
                    "inbox_error_code": inbox.last_error_code if inbox is not None else None,
                }
            )
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
