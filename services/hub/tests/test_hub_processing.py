"""Origin-hub processing, routing and holds (SHP-05, SHP-06, SHP-13, OPS-05)."""

from __future__ import annotations

from datetime import time

import pytest
from hub_fixtures import (
    OPERATIONS,
    OTHER_TRACKING,
    TRACKING,
    baghdad_hub,
    build_store,
    karbala_hub,
    linehaul_service,
    processing_service,
    sorted_parcel,
)

from hub.domain.errors import (
    ParcelAlreadyReceived,
    ParcelIsHeld,
    ParcelNotInThisHub,
    ParcelNotSorted,
    SameCityParcelDoesNotTravelBetweenHubs,
    UnknownGovernorate,
)
from hub.domain.value_objects import (
    HoldReason,
    ParcelDisposition,
    ParcelPresenceStatus,
    RoutingDecision,
    Urgency,
)

# ------------------------------------------------------------------ SHP-05


def test_a_parcel_is_scanned_in_on_arrival() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    presence = processing_service(uow).scan_in(
        hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="BAGHDAD"
    )

    assert presence.status is ParcelPresenceStatus.RECEIVED
    assert presence.destination_governorate == "BAGHDAD"


def test_the_same_parcel_is_not_scanned_in_twice() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = processing_service(uow)
    service.scan_in(
        hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="BAGHDAD"
    )

    with pytest.raises(ParcelAlreadyReceived):
        service.scan_in(
            hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="BAGHDAD"
        )


def test_an_unknown_destination_governorate_is_refused() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    with pytest.raises(UnknownGovernorate):
        processing_service(uow).scan_in(
            hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="ATLANTIS"
        )


def test_sorting_records_destination_urgency_and_route() -> None:
    """"sorted by destination city, urgency, and route" (v6.3 p.22)."""
    uow = build_store()
    hub = karbala_hub(uow)
    service = processing_service(uow)
    service.scan_in(
        hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="BAGHDAD"
    )

    presence = service.sort(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        urgency=Urgency.URGENT,
        route_code="R-14",
    )

    assert presence.urgency is Urgency.URGENT
    assert presence.route_code == "R-14"
    assert presence.status is ParcelPresenceStatus.SORTED


# ------------------------------------------------------------------ SHP-06


def test_a_same_city_parcel_skips_the_hub_to_hub_stage_entirely() -> None:
    """v6.3 p.22 — it "goes straight toward last-mile delivery"."""
    uow = build_store()
    hub = karbala_hub(uow)

    presence = sorted_parcel(uow, hub, destination="KARBALA")

    assert presence.routing_decision is RoutingDecision.SAME_CITY_DIRECT
    assert presence.status is ParcelPresenceStatus.READY_FOR_LAST_MILE


def test_an_inter_city_parcel_waits_for_a_linehaul() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    presence = sorted_parcel(uow, hub, destination="BAGHDAD")

    assert presence.routing_decision is RoutingDecision.INTER_CITY_LINEHAUL
    assert presence.status is ParcelPresenceStatus.SORTED
    assert presence.awaiting_linehaul is True


def test_the_routing_decision_is_derived_not_typed_in() -> None:
    """A counter cannot forget the same-city rule, because nobody enters it."""
    uow = build_store()
    karbala = karbala_hub(uow)
    baghdad = baghdad_hub(uow)
    same = sorted_parcel(uow, karbala, destination="KARBALA")
    across = sorted_parcel(
        uow, baghdad, tracking_code=OTHER_TRACKING, destination="KARBALA"
    )

    assert same.is_same_city is True
    assert across.is_same_city is False


def test_a_same_city_parcel_cannot_be_put_on_a_consignment() -> None:
    uow = build_store()
    karbala = karbala_hub(uow)
    baghdad = baghdad_hub(uow)
    sorted_parcel(uow, karbala, destination="KARBALA")
    consignment = linehaul_service(uow).open_consignment(
        origin_hub_id=karbala.hub_id, destination_hub_id=baghdad.hub_id
    )

    with pytest.raises(SameCityParcelDoesNotTravelBetweenHubs):
        linehaul_service(uow).add_parcel(
            consignment_id=consignment.consignment_id, tracking_code=TRACKING
        )


# ------------------------------------------------------------------ SHP-13


def test_a_checkpoint_interception_returns_the_parcel_to_the_merchant() -> None:
    """v6.3 p.24 Confirmed decision — not a judgement call."""
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)

    outcome = processing_service(uow).report_checkpoint_interception(
        hub_id=hub.hub_id, tracking_code=TRACKING, reported_by_actor_id=OPERATIONS
    )

    assert outcome.disposition is ParcelDisposition.RETURN_TO_MERCHANT
    assert outcome.presence.status is ParcelPresenceStatus.HELD


def test_the_interception_event_carries_the_fixed_disposition() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)

    outcome = processing_service(uow).report_checkpoint_interception(
        hub_id=hub.hub_id, tracking_code=TRACKING, reported_by_actor_id=OPERATIONS
    )

    payload = uow.as_committed().outbox.get_by_event_id(outcome.event_id).payload_json["payload"]
    assert payload["reason"] == "CHECKPOINT_INTERCEPTION"
    assert payload["disposition"] == "RETURN_TO_MERCHANT"


def test_a_tamper_hold_never_says_continue() -> None:
    """v6.3 p.25 — a seal mismatch is never a silent pass-through."""
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)

    outcome = processing_service(uow).hold(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        reason=HoldReason.TAMPER_INVESTIGATION,
    )

    assert outcome.disposition is not ParcelDisposition.CONTINUE


def test_a_held_parcel_cannot_be_sorted_onward() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)
    processing_service(uow).hold(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        reason=HoldReason.DAMAGED_IN_HUB,
    )

    with pytest.raises(ParcelIsHeld):
        processing_service(uow).sort(hub_id=hub.hub_id, tracking_code=TRACKING)


def test_a_parcel_that_is_not_here_cannot_be_acted_on() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    with pytest.raises(ParcelNotInThisHub):
        processing_service(uow).sort(hub_id=hub.hub_id, tracking_code=TRACKING)


# ------------------------------------------------------------------ handover


def test_a_ready_parcel_can_be_handed_to_last_mile() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub, destination="KARBALA")

    presence = processing_service(uow).hand_to_last_mile(
        hub_id=hub.hub_id, tracking_code=TRACKING
    )

    assert presence.status is ParcelPresenceStatus.HANDED_TO_LAST_MILE


def test_an_unsorted_parcel_cannot_be_handed_over() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    processing_service(uow).scan_in(
        hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="KARBALA"
    )

    with pytest.raises(ParcelNotSorted):
        processing_service(uow).hand_to_last_mile(
            hub_id=hub.hub_id, tracking_code=TRACKING
        )


def test_a_held_parcel_is_never_handed_to_a_driver() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub, destination="KARBALA")
    processing_service(uow).hold(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        reason=HoldReason.OPERATIONS_HOLD,
    )

    with pytest.raises(ParcelIsHeld):
        processing_service(uow).hand_to_last_mile(
            hub_id=hub.hub_id, tracking_code=TRACKING
        )


# ------------------------------------------------------------------ OPS-05


def test_the_activity_view_counts_backlog_ready_and_held() -> None:
    uow = build_store()
    hub = karbala_hub(uow, cut_off=time(18, 0))
    service = processing_service(uow)
    service.scan_in(
        hub_id=hub.hub_id, tracking_code=TRACKING, destination_governorate="KARBALA"
    )
    service.scan_in(
        hub_id=hub.hub_id, tracking_code=OTHER_TRACKING, destination_governorate="BAGHDAD"
    )
    service.sort(hub_id=hub.hub_id, tracking_code=TRACKING)
    service.sort(hub_id=hub.hub_id, tracking_code=OTHER_TRACKING)
    service.hold(
        hub_id=hub.hub_id,
        tracking_code=OTHER_TRACKING,
        reason=HoldReason.OPERATIONS_HOLD,
    )

    snapshot = service.activity(hub_id=hub.hub_id)

    assert snapshot.ready_for_last_mile == 1
    assert snapshot.held == 1
    assert snapshot.backlog == 0


def test_the_activity_view_counts_parcels_waiting_for_a_linehaul() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub, destination="BAGHDAD")

    snapshot = processing_service(uow).activity(hub_id=hub.hub_id)

    assert snapshot.awaiting_linehaul == 1
    assert snapshot.sorted_count == 1
