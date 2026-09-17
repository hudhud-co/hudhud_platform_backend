"""pickup.fact.handover_completed envelope generation and contract conformance."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pickup.application.handover_fact_mapper import (
    HandoverOutcome,
    build_handover_completed_envelope,
)
from pickup.domain.handover import HandoverDiscrepancyReason
from pickup.domain.value_objects import EvidenceMediaRef
from pickup.infrastructure.contracts.registry import (
    HANDOVER_COMPLETED_EVENT_TYPE,
    load_handover_completed_registry,
)

RELEASED_AT = datetime(2026, 9, 14, 15, 30, tzinfo=UTC)
PHOTO = EvidenceMediaRef(
    ref_type="hub_condition_photo",
    bucket="hudhud-evidence",
    key="pickup/handover/condition-1.jpg",
    content_type="image/jpeg",
)


def _build(**overrides):
    kwargs = {
        "pickup_task_id": uuid4(),
        "shipment_id": uuid4(),
        "handover_manifest_id": uuid4(),
        "receiving_hub_id": uuid4(),
        "outcome": HandoverOutcome.RECEIVED,
        "released_at": RELEASED_AT,
        "releasing_driver_user_id": "driver-42",
        "receiving_actor_id": "hub-operator-7",
        "aggregate_version": 4,
        "event_id": uuid4(),
        "correlation_id": uuid4(),
    }
    kwargs.update(overrides)
    return build_handover_completed_envelope(**kwargs)


def test_registry_resolves_the_contract_and_subject() -> None:
    contract = load_handover_completed_registry().contract
    assert contract.event_type == HANDOVER_COMPLETED_EVENT_TYPE
    assert contract.subject == "hudhud.pickup.pickup.fact.handover_completed.v1"
    assert contract.stream == "HUDHUD_PICKUP"
    assert contract.producer == "pickup"
    assert contract.aggregate_type == "pickup_task"


def test_a_clean_receipt_produces_a_valid_envelope() -> None:
    task_id = uuid4()
    envelope, subject = _build(pickup_task_id=task_id)
    assert subject == "hudhud.pickup.pickup.fact.handover_completed.v1"
    assert envelope["event_type"] == HANDOVER_COMPLETED_EVENT_TYPE
    assert envelope["aggregate_id"] == str(task_id)
    assert envelope["payload"]["pickup_task_id"] == str(task_id)
    assert envelope["payload"]["outcome"] == "RECEIVED"
    assert "discrepancy_reason" not in envelope["payload"]


def test_a_disputed_receipt_carries_its_evidence_as_media_refs() -> None:
    envelope, _ = _build(
        outcome=HandoverOutcome.RECEIVED_WITH_DISCREPANCY,
        discrepancy_reason=HandoverDiscrepancyReason.DAMAGED_AT_HANDOVER,
        media_refs=(PHOTO,),
    )
    assert envelope["payload"]["discrepancy_reason"] == "DAMAGED_AT_HANDOVER"
    assert len(envelope["media_refs"]) == 1
    assert "evidence_bytes" not in envelope["payload"]


def test_a_driver_can_never_record_its_own_receipt() -> None:
    with pytest.raises(ValueError, match="must differ"):
        _build(receiving_actor_id="driver-42")


def test_a_dispute_without_a_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="requires a discrepancy reason"):
        _build(outcome=HandoverOutcome.RECEIVED_WITH_DISCREPANCY, media_refs=(PHOTO,))


def test_a_dispute_without_evidence_is_refused() -> None:
    with pytest.raises(ValueError, match="media reference"):
        _build(
            outcome=HandoverOutcome.RECEIVED_WITH_DISCREPANCY,
            discrepancy_reason=HandoverDiscrepancyReason.WRONG_HUB_RECEIVED,
        )


def test_missing_from_driver_can_never_release_custody() -> None:
    with pytest.raises(ValueError, match="does not release custody"):
        _build(
            outcome=HandoverOutcome.RECEIVED_WITH_DISCREPANCY,
            discrepancy_reason=HandoverDiscrepancyReason.MISSING_FROM_DRIVER,
            media_refs=(PHOTO,),
        )


def test_the_envelope_never_claims_a_shipment_aggregate() -> None:
    envelope, _ = _build()
    assert envelope["aggregate_type"] == "pickup_task"
    assert "shipment_aggregate_version" not in envelope["payload"]
    assert envelope["payload"]["shipment_id"] != envelope["aggregate_id"]
