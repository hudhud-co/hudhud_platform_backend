"""Seed a PROOF_CAPTURED PickupTask and run PickupAcceptanceService."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.recovery_service import (
    PickupRecoveryService,
    RegisterPickupTaskCommand,
)
from pickup.domain.value_objects import (
    AcceptanceOutcome,
    EvidenceMediaRef,
    PickupTaskStatus,
    ShipmentStatus,
)
from pickup.infrastructure.fake_shipment_eligibility import InMemoryShipmentEligibilityAdapter
from pickup.infrastructure.persistence.session import build_engine, build_session_factory
from pickup.infrastructure.persistence.sqlalchemy_store import SqlAlchemyPickupUnitOfWork
from pickup.ports.shipment_eligibility import ShipmentEligibilitySnapshot


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    shipment_id_raw = os.environ.get("SHIPMENT_ID")
    driver_id = os.environ.get("DRIVER_ID")
    waybill = os.environ.get("WAYBILL")
    idempotency_key = os.environ.get("IDEMPOTENCY_KEY")
    outcome_raw = os.environ.get("OUTCOME", "ACCEPTED")
    if not all((database_url, shipment_id_raw, driver_id, waybill, idempotency_key)):
        print("required probe inputs missing", file=sys.stderr)
        return 1

    shipment_id = UUID(shipment_id_raw)
    outcome = AcceptanceOutcome(outcome_raw)
    engine = build_engine(database_url)
    try:
        eligibility = InMemoryShipmentEligibilityAdapter()
        eligibility.seed(
            ShipmentEligibilitySnapshot(
                shipment_id=shipment_id,
                shipment_status=ShipmentStatus.CREATED,
                custody_started=False,
                custody_type=None,
                custody_id=None,
            )
        )
        uow = SqlAlchemyPickupUnitOfWork(build_session_factory(engine))
        recovery = PickupRecoveryService(uow, eligibility)
        service = PickupAcceptanceService(uow)
        now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
        task_id = uuid4()
        recovery.register_pickup_task(
            RegisterPickupTaskCommand(
                pickup_task_id=task_id,
                shipment_id=shipment_id,
                assigned_driver_user_id=driver_id,
                assigned_batch_id=uuid4(),
                has_pickup_condition_proof=True,
                status=PickupTaskStatus.PROOF_CAPTURED,
                created_at=now,
            )
        )
        media_key = os.environ.get("MEDIA_KEY")
        media_refs = ()
        if media_key:
            media_refs = (
                EvidenceMediaRef(
                    ref_type="s3",
                    bucket="hudhud-evidence",
                    key=media_key,
                    content_type="image/jpeg",
                ),
            )
        result = service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=task_id,
                acting_driver_user_id=driver_id,
                scanned_identifier=waybill,
                outcome=outcome,
                idempotency_key=idempotency_key,
                accepted_at=now,
                media_refs=media_refs,
            )
        )
        payload = result.outbox_record.payload_json.get("payload") or {}
        refs = result.outbox_record.payload_json.get("media_refs") or []
        print(
            json.dumps(
                {
                    "event_id": str(result.event_id),
                    "pickup_task_id": str(task_id),
                    "shipment_id": str(shipment_id),
                    "acceptance_state": result.pickup_task.acceptance_state.value
                    if result.pickup_task.acceptance_state
                    else None,
                    "aggregate_version": result.aggregate_version,
                    "outbox_status": result.outbox_record.status.value,
                    "media_ref_count": len(refs),
                    "has_inline_media": any(
                        key in payload
                        for key in ("inline_evidence", "evidence_bytes", "exception_evidence")
                    ),
                }
            )
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
