"""Edit rules by stage, COD changes and cancellation (SHP-10, SHP-11, SHP-12)."""

from __future__ import annotations

import pytest
from ordering_fixtures import (
    COURIER,
    STORE_ID,
    amendment_service,
    at,
    build_store,
    cod_draft,
    customer_sender,
    draft,
    labelled_merchant_shipment,
    merchant_sender,
    new_id,
    receiver,
    send_service,
)

from ordering.application.amendment_service import (
    PLATFORM_FIXED_KEYS,
    ReceiverAmendment,
)
from ordering.domain.errors import (
    CodAmountNotAllowed,
    CodAmountTooLateToChange,
    FieldNotEditableAtThisStage,
    RequestAlreadyInCustody,
    SenderMayNotSetPlatformPolicy,
)
from ordering.domain.money import Money
from ordering.domain.value_objects import (
    CancellationReason,
    EditStage,
    RequestStatus,
    ShipmentAddOns,
)


def _assign_courier(uow, request_id) -> None:
    uow.begin()
    request = uow.requests.get(request_id)
    request.assigned_courier_id = COURIER
    uow.requests.save(request)
    uow.commit()


def _take_custody(uow, request_id) -> None:
    """Simulate the custody fact Shipment publishes and Ordering consumes."""
    uow.begin()
    request = uow.requests.get(request_id)
    request.custody_started_at = at(11)
    uow.requests.save(request)
    uow.commit()


def _arrive_at_door(uow, request_id) -> None:
    uow.begin()
    request = uow.requests.get(request_id)
    request.courier_at_receiver_at = at(15)
    uow.requests.save(request)
    uow.commit()


# ------------------------------------------------------------------ stages


def test_nothing_assigned_means_everything_can_change() -> None:
    """"No courier is assigned yet, so everything here can still be changed." """
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    stage = amendment_service(uow).edit_stage(request_id=request.request_id)

    assert stage is EditStage.UNASSIGNED


def test_a_booked_courier_moves_the_shipment_to_the_assigned_stage() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _assign_courier(uow, request.request_id)

    assert (
        amendment_service(uow).edit_stage(request_id=request.request_id)
        is EditStage.COURIER_ASSIGNED
    )


def test_custody_moves_the_shipment_beyond_self_service_editing() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    assert (
        amendment_service(uow).edit_stage(request_id=request.request_id)
        is EditStage.IN_CUSTODY
    )
    assert amendment_service(uow).editable_fields(request_id=request.request_id) == frozenset()


def test_a_cancelled_shipment_is_closed_to_edits() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
    )

    assert (
        amendment_service(uow).edit_stage(request_id=request.request_id)
        is EditStage.CLOSED
    )


# ------------------------------------------------------------------ self-service


def test_an_unassigned_shipment_can_be_edited_freely() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    outcome = amendment_service(uow).amend(
        request_id=request.request_id,
        description="Cotton bedsheet set, 6 pieces",
        receiver=ReceiverAmendment(name="Zahra Hussein", governorate="NAJAF"),
    )

    assert outcome.request.description == "Cotton bedsheet set, 6 pieces"
    assert outcome.request.receiver.governorate == "NAJAF"
    assert outcome.courier_released is False


def test_changing_the_pickup_address_releases_the_assigned_courier() -> None:
    """"The courier has been released. Confirm your changes and we book a new one." """
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _assign_courier(uow, request.request_id)

    outcome = amendment_service(uow).amend(
        request_id=request.request_id, pickup_store_id=new_id()
    )

    assert outcome.courier_released is True
    assert outcome.request.assigned_courier_id is None


def test_changing_the_pickup_window_releases_the_assigned_courier() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _assign_courier(uow, request.request_id)

    outcome = amendment_service(uow).amend(
        request_id=request.request_id,
        pickup_window_start=at(14),
        pickup_window_end=at(18),
    )

    assert outcome.courier_released is True


def test_editing_the_receiver_does_not_release_the_courier() -> None:
    """Only where or when the courier should come invalidates their booking."""
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _assign_courier(uow, request.request_id)

    outcome = amendment_service(uow).amend(
        request_id=request.request_id,
        receiver=ReceiverAmendment(name="Zahra Hussein"),
    )

    assert outcome.courier_released is False
    assert outcome.request.assigned_courier_id == COURIER


def test_contents_lock_once_the_parcel_is_in_custody() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    with pytest.raises(FieldNotEditableAtThisStage) as caught:
        amendment_service(uow).amend(
            request_id=request.request_id, description="Something else"
        )

    assert caught.value.field_name == "description"
    assert caught.value.stage == "IN_CUSTODY"


def test_the_pickup_address_locks_once_the_parcel_is_in_custody() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    with pytest.raises(FieldNotEditableAtThisStage):
        amendment_service(uow).amend(
            request_id=request.request_id, pickup_store_id=STORE_ID
        )


def test_a_refused_edit_changes_nothing_at_all() -> None:
    """A request mixing an allowed change with a disallowed one must not half-apply."""
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    with pytest.raises(FieldNotEditableAtThisStage):
        amendment_service(uow).amend(
            request_id=request.request_id,
            receiver=ReceiverAmendment(name="Zahra"),
            description="Something else",
        )

    uow.begin()
    stored = uow.requests.get(request.request_id)
    uow.commit()
    assert stored.receiver.name is None
    assert stored.description == "Cotton bedsheet set, 4 pieces"


@pytest.mark.parametrize("key", sorted(PLATFORM_FIXED_KEYS))
def test_a_sender_may_not_set_a_company_wide_rule(key: str) -> None:
    """v6.3 p.13 Role boundary — Sender (MER-15)."""
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    with pytest.raises(SenderMayNotSetPlatformPolicy):
        amendment_service(uow).amend(
            request_id=request.request_id, rejected_keys=(key,)
        )


def test_add_ons_can_be_changed_before_collection() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    outcome = amendment_service(uow).amend(
        request_id=request.request_id,
        add_ons=ShipmentAddOns(open_box_allowed=True, photo_documentation=True),
    )

    assert outcome.request.add_ons.open_box_allowed is True


# ------------------------------------------------------------------ SHP-12


def test_the_cod_amount_can_be_corrected_before_the_door() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=cod_draft())
    _take_custody(uow, request.request_id)

    updated = amendment_service(uow).change_cod_amount(
        request_id=request.request_id, amount=Money(210_000)
    )

    assert updated.cod_amount == Money(210_000)


def test_the_old_amount_stands_once_the_courier_is_at_the_door() -> None:
    """SHP-12 — after that the difference is settled through a claim."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=cod_draft())
    _take_custody(uow, request.request_id)
    _arrive_at_door(uow, request.request_id)

    with pytest.raises(CodAmountTooLateToChange):
        amendment_service(uow).change_cod_amount(
            request_id=request.request_id, amount=Money(210_000)
        )

    uow.begin()
    stored = uow.requests.get(request.request_id)
    uow.commit()
    assert stored.cod_amount == Money(450_000)


def test_a_non_cod_shipment_has_no_amount_to_change() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    with pytest.raises(CodAmountNotAllowed):
        amendment_service(uow).change_cod_amount(
            request_id=request.request_id, amount=Money(1000)
        )


# ------------------------------------------------------------------ support


def test_support_can_correct_the_receiver_while_the_parcel_is_moving() -> None:
    """"Support applies it before the delivery attempt." """
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    updated = amendment_service(uow).apply_support_correction(
        request_id=request.request_id,
        receiver=ReceiverAmendment(address_line="House 14, near the bakery"),
    )

    assert updated.receiver.address_line == "House 14, near the bakery"


def test_support_cannot_rewrite_the_contents_of_a_box_in_a_van() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=cod_draft())
    _take_custody(uow, request.request_id)
    _arrive_at_door(uow, request.request_id)

    with pytest.raises(CodAmountTooLateToChange):
        amendment_service(uow).apply_support_correction(
            request_id=request.request_id, cod_amount=Money(1)
        )


def test_support_cannot_correct_a_cancelled_shipment() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
    )

    with pytest.raises(FieldNotEditableAtThisStage):
        amendment_service(uow).apply_support_correction(
            request_id=request.request_id,
            receiver=ReceiverAmendment(name="Zahra"),
        )


# ------------------------------------------------------------------ SHP-10


def test_a_shipment_can_be_cancelled_before_pickup() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    cancelled, event_id = amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
    )

    assert cancelled.status is RequestStatus.CANCELLED
    assert cancelled.cancellation_reason is CancellationReason.SENDER_CHANGED_MIND
    assert uow.outbox.get_by_event_id(event_id) is not None


def test_a_parcel_already_in_custody_cannot_be_cancelled_here() -> None:
    """Cancelling it would leave a box in a van that no context believes exists."""
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _take_custody(uow, request.request_id)

    with pytest.raises(RequestAlreadyInCustody):
        amendment_service(uow).cancel(
            request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
        )


def test_a_cancelled_shipment_cannot_be_cancelled_twice() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    service = amendment_service(uow)
    service.cancel(
        request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
    )

    with pytest.raises(RequestAlreadyInCustody):
        service.cancel(
            request_id=request.request_id, reason=CancellationReason.DUPLICATE
        )


def test_a_cancellation_event_records_whether_a_courier_must_be_released() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    _assign_courier(uow, request.request_id)

    _, event_id = amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.OPERATIONS_DECISION
    )

    payload = uow.outbox.get_by_event_id(event_id).payload_json["payload"]
    assert payload["had_courier_assigned"] is True
    assert payload["reason"] == "OPERATIONS_DECISION"


def test_a_customer_drop_off_can_also_be_cancelled() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft(receiver=receiver()))

    cancelled, _ = amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.NOT_DROPPED_IN_TIME
    )

    assert cancelled.status is RequestStatus.CANCELLED
