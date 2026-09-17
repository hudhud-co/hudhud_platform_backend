"""Consignments, the per-hub cut-off, seal checks and linehaul (SHP-07, SHP-08, OPS-01/02)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest
from hub_fixtures import (
    LINEHAUL_DRIVER,
    OPERATOR,
    OTHER_TRACKING,
    TRACKING,
    at,
    baghdad_hub,
    build_store,
    karbala_hub,
    linehaul_service,
    processing_service,
    sorted_parcel,
)

from hub.application.linehaul_service import LinehaulService
from hub.domain.errors import (
    ConsignmentAlreadySealed,
    ConsignmentIsEmpty,
    ConsignmentTransitionNotAllowed,
    CutOffNotReached,
    LinehaulDriverMayNotSplitABatch,
    SealCodeInvalid,
    SealMismatchRequiresInvestigation,
    UnsealedConsignmentHasNothingToCheck,
)
from hub.domain.value_objects import (
    ConsignmentStatus,
    GeoPoint,
    LinehaulStatus,
    ParcelPresenceStatus,
    PositionSource,
    SealCheckOutcome,
)


def _grouped(uow, *, cut_off: time = time(18, 0), parcels: int = 1):
    karbala = karbala_hub(uow, cut_off=cut_off)
    baghdad = baghdad_hub(uow)
    codes = [TRACKING, OTHER_TRACKING][:parcels]
    for code in codes:
        sorted_parcel(uow, karbala, tracking_code=code, destination="BAGHDAD")
    service = linehaul_service(uow)
    consignment = service.open_consignment(
        origin_hub_id=karbala.hub_id, destination_hub_id=baghdad.hub_id
    )
    for code in codes:
        service.add_parcel(
            consignment_id=consignment.consignment_id, tracking_code=code
        )
    return karbala, baghdad, service.get_consignment(consignment.consignment_id)


# ------------------------------------------------------------------ grouping


def test_sorted_parcels_are_grouped_for_the_move() -> None:
    uow = build_store()
    karbala, _, consignment = _grouped(uow, parcels=2)

    assert consignment.parcel_count == 2
    presence = uow.as_committed().presences.find_in_hub(karbala.hub_id, TRACKING)
    assert presence.status is ParcelPresenceStatus.GROUPED


def test_a_consignment_with_nothing_in_it_cannot_be_dispatched() -> None:
    uow = build_store()
    karbala = karbala_hub(uow)
    baghdad = baghdad_hub(uow)
    consignment = linehaul_service(uow).open_consignment(
        origin_hub_id=karbala.hub_id, destination_hub_id=baghdad.hub_id
    )

    with pytest.raises(ConsignmentIsEmpty):
        linehaul_service(uow).dispatch(
            consignment_id=consignment.consignment_id, moment=at(19)
        )


# ------------------------------------------------------------------ SHP-07


def test_a_consignment_waits_for_its_own_hubs_cut_off() -> None:
    """v6.3 p.23 — "set per hub, not company-wide"."""
    uow = build_store()
    _, _, consignment = _grouped(uow, cut_off=time(18, 0))

    with pytest.raises(CutOffNotReached) as caught:
        linehaul_service(uow).dispatch(
            consignment_id=consignment.consignment_id, moment=at(17, 30)
        )

    assert caught.value.cut_off == "18:00"


def test_dispatch_is_allowed_once_that_hubs_cut_off_has_passed() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow, cut_off=time(18, 0))

    dispatched = linehaul_service(uow).dispatch(
        consignment_id=consignment.consignment_id, moment=at(19)
    )

    assert dispatched.status is ConsignmentStatus.DISPATCHED


def test_two_hubs_can_have_different_cut_offs() -> None:
    """The whole point of the v6.3 change: 18:30 is late for one hub and early for another."""
    early_uow = build_store()
    _, _, early = _grouped(early_uow, cut_off=time(18, 0))
    late_uow = build_store()
    _, _, late = _grouped(late_uow, cut_off=time(20, 30))

    linehaul_service(early_uow).dispatch(
        consignment_id=early.consignment_id, moment=at(18, 30)
    )
    with pytest.raises(CutOffNotReached):
        linehaul_service(late_uow).dispatch(
            consignment_id=late.consignment_id, moment=at(18, 30)
        )


def test_operations_can_override_the_cut_off_explicitly() -> None:
    """An auditable choice, not a silent bypass."""
    uow = build_store()
    _, _, consignment = _grouped(uow, cut_off=time(23, 59))

    dispatched = linehaul_service(uow).dispatch(
        consignment_id=consignment.consignment_id,
        moment=at(10),
        override_cut_off=True,
    )

    assert dispatched.status is ConsignmentStatus.DISPATCHED


def test_dispatch_moves_every_parcel_in_the_group() -> None:
    uow = build_store()
    karbala, _, consignment = _grouped(uow, parcels=2)
    linehaul_service(uow).dispatch(
        consignment_id=consignment.consignment_id, moment=at(19)
    )

    for code in (TRACKING, OTHER_TRACKING):
        assert (
            uow.as_committed().presences.find_in_hub(karbala.hub_id, code).status
            is ParcelPresenceStatus.DEPARTED
        )


# ------------------------------------------------------------------ sealing


def test_sealing_is_optional_and_a_consignment_can_travel_unsealed() -> None:
    """v6.3 p.22 — "optional, left to the hub's discretion"."""
    uow = build_store()
    _, _, consignment = _grouped(uow)

    dispatched = linehaul_service(uow).dispatch(
        consignment_id=consignment.consignment_id, moment=at(19)
    )

    assert dispatched.is_sealed is False
    assert dispatched.status is ConsignmentStatus.DISPATCHED


def test_a_seal_records_who_applied_it_and_when() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)

    sealed = linehaul_service(uow).apply_seal(
        consignment_id=consignment.consignment_id,
        seal_code="seal-0001",
        operator_principal_id=OPERATOR,
    )

    assert sealed.seal.seal_code == "SEAL-0001"
    assert sealed.seal.applied_by_actor_id == OPERATOR
    assert sealed.status is ConsignmentStatus.SEALED


def test_a_consignment_cannot_be_sealed_twice() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    service.apply_seal(
        consignment_id=consignment.consignment_id,
        seal_code="SEAL-0001",
        operator_principal_id=OPERATOR,
    )

    with pytest.raises(ConsignmentAlreadySealed):
        service.apply_seal(
            consignment_id=consignment.consignment_id,
            seal_code="SEAL-0002",
            operator_principal_id=OPERATOR,
        )


def test_a_malformed_seal_code_is_refused() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)

    with pytest.raises(SealCodeInvalid):
        linehaul_service(uow).apply_seal(
            consignment_id=consignment.consignment_id,
            seal_code="x",
            operator_principal_id=OPERATOR,
        )


# ------------------------------------------------------------------ SHP-08


def _arrived_sealed(uow, seal: str = "SEAL-0001"):
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    service.apply_seal(
        consignment_id=consignment.consignment_id,
        seal_code=seal,
        operator_principal_id=OPERATOR,
    )
    service.dispatch(consignment_id=consignment.consignment_id, moment=at(19))
    service.record_arrival(consignment_id=consignment.consignment_id, moment=at(5, day=15))
    return service, consignment


def test_a_matching_seal_passes_and_the_consignment_reconciles() -> None:
    uow = build_store()
    service, consignment = _arrived_sealed(uow)

    result = service.check_seal(
        consignment_id=consignment.consignment_id,
        observed_seal_code="SEAL-0001",
        operator_principal_id=OPERATOR,
    )
    reconciled = service.reconcile(consignment_id=consignment.consignment_id)

    assert result.check.outcome is SealCheckOutcome.INTACT
    assert result.opened_investigation is False
    assert reconciled.status is ConsignmentStatus.RECONCILED


def test_a_mismatched_seal_opens_a_tamper_investigation() -> None:
    """v6.3 p.25 — never a silent pass-through."""
    uow = build_store()
    service, consignment = _arrived_sealed(uow)

    result = service.check_seal(
        consignment_id=consignment.consignment_id,
        observed_seal_code="SEAL-9999",
        operator_principal_id=OPERATOR,
    )

    assert result.check.outcome is SealCheckOutcome.MISMATCHED
    assert result.opened_investigation is True
    assert result.consignment.status is ConsignmentStatus.UNDER_TAMPER_INVESTIGATION


def test_a_missing_seal_also_opens_an_investigation() -> None:
    uow = build_store()
    service, consignment = _arrived_sealed(uow)

    result = service.check_seal(
        consignment_id=consignment.consignment_id,
        observed_seal_code=None,
        operator_principal_id=OPERATOR,
    )

    assert result.check.outcome is SealCheckOutcome.MISSING
    assert result.opened_investigation is True


def test_a_consignment_under_investigation_cannot_be_reconciled_away() -> None:
    uow = build_store()
    service, consignment = _arrived_sealed(uow)
    service.check_seal(
        consignment_id=consignment.consignment_id,
        observed_seal_code="SEAL-9999",
        operator_principal_id=OPERATOR,
    )

    with pytest.raises(SealMismatchRequiresInvestigation):
        service.reconcile(consignment_id=consignment.consignment_id)


def test_an_unsealed_consignment_has_no_seal_to_check() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    service.dispatch(consignment_id=consignment.consignment_id, moment=at(19))
    service.record_arrival(consignment_id=consignment.consignment_id, moment=at(5, day=15))

    with pytest.raises(UnsealedConsignmentHasNothingToCheck):
        service.check_seal(
            consignment_id=consignment.consignment_id,
            observed_seal_code="ANY-0001",
            operator_principal_id=OPERATOR,
        )


def test_reconciling_moves_parcels_into_the_destination_hub() -> None:
    uow = build_store()
    _, baghdad, consignment = _grouped(uow)
    service = linehaul_service(uow)
    service.dispatch(consignment_id=consignment.consignment_id, moment=at(19))
    service.record_arrival(consignment_id=consignment.consignment_id, moment=at(5, day=15))
    service.reconcile(consignment_id=consignment.consignment_id)

    presence = uow.as_committed().presences.find_in_hub(baghdad.hub_id, TRACKING)
    assert presence is not None
    assert presence.status is ParcelPresenceStatus.READY_FOR_LAST_MILE


def test_an_arrival_cannot_be_recorded_before_dispatch() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)

    with pytest.raises(ConsignmentTransitionNotAllowed):
        linehaul_service(uow).record_arrival(
            consignment_id=consignment.consignment_id, moment=at(5, day=15)
        )


# ------------------------------------------------------------------ OPS-01/02


def test_both_position_sources_are_recorded_as_themselves() -> None:
    """v6.3 p.24 keeps the driver's device as a second, independent confirmation."""
    uow = build_store()
    karbala, baghdad, consignment = _grouped(uow)
    service = linehaul_service(uow)
    linehaul = service.plan_linehaul(
        consignment_id=consignment.consignment_id,
        vehicle_reference="VAN-07",
        driver_principal_id=LINEHAUL_DRIVER,
    )
    point = GeoPoint(latitude=Decimal("32.6"), longitude=Decimal("44.0"))
    service.record_position(
        linehaul_id=linehaul.linehaul_id,
        source=PositionSource.VEHICLE_TRACKER,
        point=point,
    )
    service.record_position(
        linehaul_id=linehaul.linehaul_id,
        source=PositionSource.DRIVER_DEVICE,
        point=point,
    )

    sources = {p.source for p in service.positions_for(linehaul_id=linehaul.linehaul_id)}
    assert sources == {PositionSource.VEHICLE_TRACKER, PositionSource.DRIVER_DEVICE}


def test_a_route_deviation_is_flagged_and_the_eta_is_revised() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    linehaul = service.plan_linehaul(
        consignment_id=consignment.consignment_id,
        vehicle_reference="VAN-07",
        driver_principal_id=LINEHAUL_DRIVER,
        expected_arrival_at=at(5, day=15),
    )

    flagged = service.flag_deviation(
        linehaul_id=linehaul.linehaul_id,
        note="diverted at the checkpoint",
        revised_arrival_at=at(7, day=15),
    )

    assert flagged.route_deviation_flagged is True
    assert flagged.expected_arrival_at == at(7, day=15)


def test_lateness_is_measured_against_the_current_eta() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    linehaul = service.plan_linehaul(
        consignment_id=consignment.consignment_id,
        vehicle_reference="VAN-07",
        driver_principal_id=LINEHAUL_DRIVER,
        expected_arrival_at=at(5, day=15),
    )
    service.depart(linehaul_id=linehaul.linehaul_id, moment=at(19))
    moving = service.get_linehaul(linehaul.linehaul_id)

    assert moving.delay_minutes(at(5, day=15)) == 0
    assert moving.delay_minutes(at(6, day=15)) == 60


def test_the_activity_view_counts_delayed_linehauls() -> None:
    uow = build_store()
    karbala, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    linehaul = service.plan_linehaul(
        consignment_id=consignment.consignment_id,
        vehicle_reference="VAN-07",
        driver_principal_id=LINEHAUL_DRIVER,
        expected_arrival_at=at(5, day=15),
    )
    service.depart(linehaul_id=linehaul.linehaul_id, moment=at(19))

    snapshot = processing_service(uow).activity(
        hub_id=karbala.hub_id, moment=at(8, day=15)
    )

    assert snapshot.delayed_linehauls == 1


def test_arrival_closes_the_linehaul_too() -> None:
    uow = build_store()
    _, _, consignment = _grouped(uow)
    service = linehaul_service(uow)
    linehaul = service.plan_linehaul(
        consignment_id=consignment.consignment_id,
        vehicle_reference="VAN-07",
        driver_principal_id=LINEHAUL_DRIVER,
    )
    service.dispatch(consignment_id=consignment.consignment_id, moment=at(19))
    service.depart(linehaul_id=linehaul.linehaul_id, moment=at(19, 10))
    service.record_arrival(consignment_id=consignment.consignment_id, moment=at(5, day=15))

    assert service.get_linehaul(linehaul.linehaul_id).status is LinehaulStatus.ARRIVED


# ------------------------------------------------------------------ role boundary


def test_a_linehaul_driver_may_not_split_a_grouped_batch() -> None:
    """v6.3 p.24 Role boundary — Linehaul Driver."""
    with pytest.raises(LinehaulDriverMayNotSplitABatch):
        LinehaulService.assert_driver_may_not_split_batch()
