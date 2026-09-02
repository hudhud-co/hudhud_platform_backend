"""Acceptance lifecycle invariant tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    CreateOrderIntentCommand,
    RecordAcceptanceScanCommand,
)
from shipment.domain.errors import (
    AcceptanceAlreadyRecorded,
    ExceptionEvidenceRequired,
    InlineMediaNotAllowed,
)
from shipment.domain.value_objects import (
    AcceptanceOutcome,
    ApproximateParcelMetrics,
    EvidenceReference,
    PackagingSealAssessment,
)
from shipment.infrastructure.memory import InMemoryShipmentRepository


def _service() -> AcceptanceLifecycleService:
    return AcceptanceLifecycleService(InMemoryShipmentRepository())


def _scan_time() -> datetime:
    return datetime(2026, 5, 31, 10, 0, tzinfo=UTC)


def _assessment() -> PackagingSealAssessment:
    return PackagingSealAssessment(
        packaging_condition="intact",
        seal_assessment="seal_present",
    )


def _evidence(
    uri: str = "s3://proof-bucket/parcel-photo-001.jpg",
    *,
    captured_at: datetime | None = None,
    location_label: str | None = "pickup-point-alpha",
) -> EvidenceReference:
    return EvidenceReference.from_reference(
        uri,
        captured_at=captured_at or _scan_time(),
        location_label=location_label,
    )


def test_new_order_has_no_custody_or_sla_start() -> None:
    service = _service()
    order_id = uuid4()
    created_at = datetime(2026, 5, 31, 9, 0, tzinfo=UTC)

    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=order_id,
            waybill_number="WB-1001",
            created_at=created_at,
        )
    )

    assert shipment.custody_active is False
    assert shipment.sla_active is False
    assert shipment.in_hodhod_network is False
    assert shipment.acceptance_record is None
    assert shipment.order_created_at == created_at


def test_accepted_starts_custody_and_sla_at_scan_time() -> None:
    service = _service()
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-2001",
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    scan_timestamp = _scan_time()

    record = service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            waybill_number="WB-2001",
            scan_timestamp=scan_timestamp,
            responsible_operator_id="operator-42",
            packaging_seal_assessment=_assessment(),
            approximate_metrics=ApproximateParcelMetrics(weight_kg=Decimal("1.25")),
            parcel_condition_evidence=(_evidence(),),
            exception_evidence=(),
            outcome=AcceptanceOutcome.ACCEPTED,
            recorded_at=scan_timestamp,
        )
    )

    updated = service._repository.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.custody_started_at == scan_timestamp
    assert updated.sla_started_at == scan_timestamp
    assert updated.in_hodhod_network is True
    assert record.outcome is AcceptanceOutcome.ACCEPTED
    assert record.responsible_operator_id == "operator-42"


def test_accepted_with_exception_requires_and_preserves_exception_evidence() -> None:
    service = _service()
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-3001",
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    scan_timestamp = _scan_time()
    exception_evidence = _evidence("s3://proof-bucket/exception-note-001.jpg")

    with pytest.raises(ExceptionEvidenceRequired):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                waybill_number="WB-3001",
                scan_timestamp=scan_timestamp,
                responsible_operator_id="operator-7",
                packaging_seal_assessment=_assessment(),
                approximate_metrics=None,
                parcel_condition_evidence=(_evidence(),),
                exception_evidence=(),
                outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
                recorded_at=scan_timestamp,
            )
        )

    record = service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            waybill_number="WB-3001",
            scan_timestamp=scan_timestamp,
            responsible_operator_id="operator-7",
            packaging_seal_assessment=_assessment(),
            approximate_metrics=None,
            parcel_condition_evidence=(_evidence(),),
            exception_evidence=(exception_evidence,),
            outcome=AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION,
            recorded_at=scan_timestamp,
        )
    )

    updated = service._repository.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.in_hodhod_network is True
    assert updated.custody_active is True
    assert updated.sla_active is True
    assert record.exception_evidence == (exception_evidence,)
    assert record.outcome is AcceptanceOutcome.ACCEPTED_WITH_EXCEPTION


def test_rejected_starts_neither_custody_nor_sla() -> None:
    service = _service()
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-4001",
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    scan_timestamp = _scan_time()

    record = service.record_acceptance_scan(
        RecordAcceptanceScanCommand(
            shipment_id=shipment.shipment_id,
            waybill_number="WB-4001",
            scan_timestamp=scan_timestamp,
            responsible_operator_id="operator-9",
            packaging_seal_assessment=_assessment(),
            approximate_metrics=None,
            parcel_condition_evidence=(_evidence(),),
            exception_evidence=(),
            outcome=AcceptanceOutcome.REJECTED,
            recorded_at=scan_timestamp,
        )
    )

    updated = service._repository.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.custody_active is False
    assert updated.sla_active is False
    assert updated.in_hodhod_network is False
    assert record.outcome is AcceptanceOutcome.REJECTED


def test_evidence_without_timestamp_or_location_is_retained_and_marked_low_trust() -> None:
    evidence = EvidenceReference.from_reference(
        "s3://proof-bucket/parcel-photo-low-trust.jpg",
        captured_at=None,
        location_label=None,
    )

    assert evidence.low_trust is True
    assert "missing_timestamp" in evidence.low_trust_reasons
    assert "missing_location" in evidence.low_trust_reasons


def test_inline_media_bytes_are_rejected() -> None:
    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference(b"inline-bytes")  # type: ignore[arg-type]

    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference("data:image/jpeg;base64,abcd")

    inline_base64 = "A" * 300
    with pytest.raises(InlineMediaNotAllowed):
        EvidenceReference.from_reference(inline_base64)


def test_repeated_acceptance_cannot_silently_overwrite() -> None:
    service = _service()
    _order, shipment = service.create_order_intent(
        CreateOrderIntentCommand(
            order_id=uuid4(),
            waybill_number="WB-5001",
            created_at=datetime(2026, 5, 31, 9, 0, tzinfo=UTC),
        )
    )
    scan_timestamp = _scan_time()
    command = RecordAcceptanceScanCommand(
        shipment_id=shipment.shipment_id,
        waybill_number="WB-5001",
        scan_timestamp=scan_timestamp,
        responsible_operator_id="operator-1",
        packaging_seal_assessment=_assessment(),
        approximate_metrics=None,
        parcel_condition_evidence=(_evidence(),),
        exception_evidence=(),
        outcome=AcceptanceOutcome.ACCEPTED,
        recorded_at=scan_timestamp,
    )
    first_record = service.record_acceptance_scan(command)

    with pytest.raises(AcceptanceAlreadyRecorded):
        service.record_acceptance_scan(
            RecordAcceptanceScanCommand(
                shipment_id=shipment.shipment_id,
                waybill_number="WB-5001",
                scan_timestamp=scan_timestamp,
                responsible_operator_id="operator-2",
                packaging_seal_assessment=_assessment(),
                approximate_metrics=None,
                parcel_condition_evidence=(_evidence("s3://proof-bucket/other.jpg"),),
                exception_evidence=(),
                outcome=AcceptanceOutcome.REJECTED,
                recorded_at=scan_timestamp,
            )
        )

    updated = service._repository.get_shipment(shipment.shipment_id)
    assert updated is not None
    assert updated.acceptance_record is not None
    assert updated.acceptance_record.record_id == first_record.record_id
    assert updated.acceptance_record.outcome is AcceptanceOutcome.ACCEPTED
