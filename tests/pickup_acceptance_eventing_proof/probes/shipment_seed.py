"""Seed a CREATED Shipment for native pickup.fact.accepted apply."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shipment.domain.entities import Shipment
from shipment.domain.value_objects import ShipmentStatus, WaybillIdentity
from shipment.infrastructure.persistence.accepted_fact_uow import SqlAlchemyAcceptedFactStore
from shipment.infrastructure.persistence.session import build_engine, build_session_factory


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    shipment_id_raw = os.environ.get("SHIPMENT_ID")
    waybill = os.environ.get("WAYBILL")
    if not database_url or not shipment_id_raw or not waybill:
        print("required probe inputs missing", file=sys.stderr)
        return 1

    shipment_id = UUID(shipment_id_raw)
    engine = build_engine(database_url)
    try:
        store = SqlAlchemyAcceptedFactStore(build_session_factory(engine))
        store.persist_created_shipment(
            Shipment(
                shipment_id=shipment_id,
                order_id=uuid4(),
                waybill_identity=WaybillIdentity(
                    waybill_number=waybill,
                    shipment_id=str(shipment_id),
                ),
                current_status=ShipmentStatus.CREATED,
                order_created_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
                version=1,
            )
        )
        print(json.dumps({"seeded": True, "status": "CREATED"}))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
