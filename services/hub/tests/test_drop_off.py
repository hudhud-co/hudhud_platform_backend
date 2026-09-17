"""The customer drop-off path (CUS-03 … CUS-07)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from hub_fixtures import (
    CUSTOMER,
    OPERATOR,
    TRACKING,
    accepted_drop_off,
    admin_service,
    at,
    build_store,
    drop_off_service,
    karbala_hub,
    new_id,
    walk_in_details,
)

from hub.application.drop_off_service import DropOffPolicy
from hub.domain.errors import (
    CustomerMayNotLabelTheirOwnParcel,
    DropOffDetailsRequired,
    DropOffExpired,
    DropOffNotLabelled,
    DropOffTransitionNotAllowed,
    HubNotActive,
    HubNotFound,
    InvalidLabelCode,
    WeightRequiredAtDropOff,
)
from hub.domain.value_objects import DropOffStatus

# ------------------------------------------------------------------ CUS-04


def test_a_customer_who_entered_details_in_advance_is_expected() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    drop_off = drop_off_service(uow).expect_drop_off(
        hub_id=hub.hub_id, tracking_code=TRACKING, shipment_request_id=new_id()
    )

    assert drop_off.status is DropOffStatus.EXPECTED
    assert drop_off.shipment_request_id is not None


def test_a_customer_who_arrives_with_nothing_has_details_taken_on_the_spot() -> None:
    """v6.3 p.18 — "If details weren't entered in advance, hub staff take them"."""
    uow = build_store()
    hub = karbala_hub(uow)

    drop_off = drop_off_service(uow).capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    assert drop_off.status is DropOffStatus.DETAILS_CAPTURED
    assert drop_off.shipment_request_id is None
    assert drop_off.captured_details["destination_governorate"] == "BAGHDAD"


def test_details_taken_at_the_counter_need_a_phone_and_a_governorate() -> None:
    """The same minimum v6.3 p.12 sets for any shipment."""
    uow = build_store()
    hub = karbala_hub(uow)

    with pytest.raises(DropOffDetailsRequired) as caught:
        drop_off_service(uow).capture_details(
            hub_id=hub.hub_id, tracking_code=TRACKING, details={}
        )

    assert set(caught.value.missing) == {"receiver_phone", "destination_governorate"}


def test_confirming_details_submitted_in_advance_keeps_the_same_drop_off() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    expected = service.expect_drop_off(hub_id=hub.hub_id, tracking_code=TRACKING)

    confirmed = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    assert confirmed.drop_off_id == expected.drop_off_id
    assert confirmed.status is DropOffStatus.DETAILS_CAPTURED


# ------------------------------------------------------------------ CUS-05


def test_hub_staff_stick_the_label_not_the_customer() -> None:
    """v6.3 p.18, CHANGED IN V6.3."""
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    with pytest.raises(CustomerMayNotLabelTheirOwnParcel):
        service.apply_label(
            drop_off_id=drop_off.drop_off_id,
            label_code="HH-000001",
            weight_grams=1400,
            operator_principal_id=CUSTOMER,
            actor_is_hub_staff=False,
        )


def test_the_operator_who_labelled_the_parcel_is_recorded() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    labelled = service.apply_label(
        drop_off_id=drop_off.drop_off_id,
        label_code="hh-000001",
        weight_grams=1400,
        operator_principal_id=OPERATOR,
        actor_is_hub_staff=True,
    )

    assert labelled.label_code == "HH-000001"
    assert labelled.labelled_by_actor_id == OPERATOR
    assert labelled.status is DropOffStatus.LABELLED


def test_a_malformed_label_code_is_refused() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    with pytest.raises(InvalidLabelCode):
        service.apply_label(
            drop_off_id=drop_off.drop_off_id,
            label_code="x",
            weight_grams=1400,
            operator_principal_id=OPERATOR,
            actor_is_hub_staff=True,
        )


# ------------------------------------------------------------------ CUS-07


def test_the_hub_weighs_the_parcel_before_labelling_it() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    with pytest.raises(WeightRequiredAtDropOff):
        service.apply_label(
            drop_off_id=drop_off.drop_off_id,
            label_code="HH-000001",
            weight_grams=0,
            operator_principal_id=OPERATOR,
            actor_is_hub_staff=True,
        )


def test_the_recorded_weight_survives_to_acceptance() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    result = accepted_drop_off(uow, hub)

    assert result.drop_off.weight_grams == 1400


# ------------------------------------------------------------------ acceptance


def test_acceptance_is_where_custody_begins() -> None:
    """v6.3 p.18 — "custody begins, and it proceeds into hub processing"."""
    uow = build_store()
    hub = karbala_hub(uow)

    result = accepted_drop_off(uow, hub)

    assert result.drop_off.status is DropOffStatus.ACCEPTED
    assert result.drop_off.accepted_by_actor_id == OPERATOR
    assert uow.as_committed().outbox.get_by_event_id(result.event_id) is not None


def test_an_unlabelled_parcel_cannot_be_accepted() -> None:
    """Custody cannot begin on a box that is not linked to a record."""
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    with pytest.raises(DropOffNotLabelled):
        service.accept(
            drop_off_id=drop_off.drop_off_id, operator_principal_id=OPERATOR
        )


def test_acceptance_is_recorded_exactly_once() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    result = accepted_drop_off(uow, hub)

    with pytest.raises(DropOffTransitionNotAllowed):
        drop_off_service(uow).accept(
            drop_off_id=result.drop_off.drop_off_id, operator_principal_id=OPERATOR
        )

    assert len(uow.as_committed().outbox.list_pending()) == 1


# ------------------------------------------------------------------ CUS-06


def test_an_unclaimed_drop_off_lapses_after_three_days() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.expect_drop_off(hub_id=hub.hub_id, tracking_code=TRACKING)

    expired = service.expire_unclaimed(
        hub_id=hub.hub_id, moment=drop_off.expires_at + timedelta(minutes=1)
    )

    assert expired == 1
    assert service.get(drop_off.drop_off_id).status is DropOffStatus.EXPIRED


def test_a_drop_off_does_not_lapse_early() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.expect_drop_off(hub_id=hub.hub_id, tracking_code=TRACKING)

    assert (
        service.expire_unclaimed(
            hub_id=hub.hub_id, moment=drop_off.expires_at - timedelta(hours=1)
        )
        == 0
    )


def test_the_hold_window_is_configurable_and_defaults_to_three_days() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    default = drop_off_service(uow).expect_drop_off(
        hub_id=hub.hub_id, tracking_code=TRACKING
    )

    assert (default.expires_at - default.created_at).days == 3

    shorter = drop_off_service(uow, policy=DropOffPolicy(hold_days=1)).expect_drop_off(
        hub_id=hub.hub_id, tracking_code="SHP-20260914-000999"
    )
    assert (shorter.expires_at - shorter.created_at).days == 1


def test_a_labelled_parcel_on_the_counter_never_lapses() -> None:
    """It is physically there — expiry is for parcels nobody brought in."""
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )
    service.apply_label(
        drop_off_id=drop_off.drop_off_id,
        label_code="HH-000001",
        weight_grams=1400,
        operator_principal_id=OPERATOR,
        actor_is_hub_staff=True,
    )

    assert service.expire_unclaimed(hub_id=hub.hub_id, moment=at(day=30)) == 0


def test_a_lapsed_drop_off_cannot_be_revived_by_labelling_it() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow, policy=DropOffPolicy(hold_days=0))
    drop_off = service.expect_drop_off(hub_id=hub.hub_id, tracking_code=TRACKING)

    with pytest.raises(DropOffExpired):
        service.apply_label(
            drop_off_id=drop_off.drop_off_id,
            label_code="HH-000001",
            weight_grams=1400,
            operator_principal_id=OPERATOR,
            actor_is_hub_staff=True,
        )


# ------------------------------------------------------------------ hub state


def test_a_drop_off_needs_a_known_hub() -> None:
    uow = build_store()

    with pytest.raises(HubNotFound):
        drop_off_service(uow).expect_drop_off(
            hub_id=new_id(), tracking_code=TRACKING
        )


def test_a_closed_hub_takes_no_drop_offs() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    admin_service(uow).set_active(hub_id=hub.hub_id, is_active=False)

    with pytest.raises(HubNotActive):
        drop_off_service(uow).expect_drop_off(
            hub_id=hub.hub_id, tracking_code=TRACKING
        )
