"""Order creation and the v6.3 sender-kind rules (CUS-01, CUS-02, MER-05/08/09/13/19)."""

from __future__ import annotations

import pytest
from ordering_fixtures import (
    MERCHANT_ID,
    at,
    build_store,
    catalogue_service,
    cod_draft,
    customer_sender,
    draft,
    merchant_sender,
    receiver,
    send_service,
)

from ordering.domain.entities import SenderRef
from ordering.domain.errors import (
    CashOnDeliveryNotAvailableForCustomers,
    CodAmountNotAllowed,
    CodAmountRequired,
    DescriptionRequired,
    InvalidLabelCode,
    InvalidPhoneNumber,
    LabelAlreadyLinked,
    LabelNotAllowedForCustomer,
    OrderNotOpen,
    PickupBlockedByMissingLabels,
    PickupNotAvailableForCustomers,
    ProhibitedGoods,
    ProhibitedGoodsNotAcknowledged,
    RequestTransitionNotAllowed,
    UnknownGoodsCategory,
    UnknownGovernorate,
)
from ordering.domain.money import Money
from ordering.domain.value_objects import (
    OrderStatus,
    ParcelMeasurements,
    PaymentTerms,
    RequestStatus,
    SenderKind,
    is_tracking_code,
)

# ------------------------------------------------------------------ CUS-01


def test_any_individual_can_create_a_shipment_without_merchant_status() -> None:
    """v6.3 p.8 — a regular customer sends without being a merchant."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())

    request = service.add_shipment(order_id=order.order_id, draft=draft())

    assert request.status is RequestStatus.AWAITING_DROPOFF
    assert is_tracking_code(request.tracking_code)


def test_an_order_can_hold_more_than_one_shipment() -> None:
    """v6.3 p.14 — the order is the agreement, each shipment is a parcel."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    first = service.add_shipment(order_id=order.order_id, draft=draft())
    second = service.add_shipment(order_id=order.order_id, draft=draft())

    assert first.order_id == second.order_id == order.order_id
    assert first.tracking_code != second.tracking_code


def test_an_order_starts_open() -> None:
    uow = build_store()
    order = send_service(uow).open_order(sender=merchant_sender())
    assert order.status is OrderStatus.OPEN
    assert order.reference.startswith("ORD-")


# ------------------------------------------------------------------ CUS-02


def test_a_regular_customers_parcel_has_no_cod_option() -> None:
    """v6.3 p.8, Confirmed decision."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())

    with pytest.raises(CashOnDeliveryNotAvailableForCustomers):
        service.add_shipment(order_id=order.order_id, draft=cod_draft())


def test_a_customer_cannot_slip_cod_through_with_a_zero_amount() -> None:
    """The refusal is about the sender kind, not the number."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())

    with pytest.raises(CashOnDeliveryNotAvailableForCustomers):
        service.add_shipment(order_id=order.order_id, draft=cod_draft(amount=0))


def test_a_merchant_may_send_cash_on_delivery() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(order_id=order.order_id, draft=cod_draft())

    assert request.payment_terms is PaymentTerms.CASH_ON_DELIVERY
    assert request.cod_amount == Money(450_000)


def test_a_cod_shipment_must_state_an_amount() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(CodAmountRequired):
        service.add_shipment(
            order_id=order.order_id,
            draft=draft(payment_terms=PaymentTerms.CASH_ON_DELIVERY),
        )


def test_a_prepaid_shipment_cannot_carry_a_cod_amount() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(CodAmountNotAllowed):
        service.add_shipment(
            order_id=order.order_id,
            draft=draft(payment_terms=PaymentTerms.PREPAID, cod_amount=Money(1000)),
        )


@pytest.mark.parametrize(
    "terms", [PaymentTerms.PREPAID, PaymentTerms.POSTPAID, PaymentTerms.CASH_ON_DELIVERY]
)
def test_v63_offers_exactly_three_payment_terms(terms: PaymentTerms) -> None:
    assert terms.value in {"PREPAID", "POSTPAID", "CASH_ON_DELIVERY"}


def test_there_are_only_three_payment_terms() -> None:
    assert len(list(PaymentTerms)) == 3


# ------------------------------------------------------------------ MER-08


def test_a_description_is_required() -> None:
    """v6.3 p.12 — so the parcel isn't registered as an unknown item."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(DescriptionRequired):
        service.add_shipment(order_id=order.order_id, draft=draft(description="   "))


def test_weight_and_size_are_optional() -> None:
    """v6.3 p.12, Confirmed decision — the description carries the requirement instead."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(order_id=order.order_id, draft=draft())

    assert request.measurements.is_empty
    assert request.description


def test_measurements_are_kept_when_given() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(
        order_id=order.order_id,
        draft=draft(measurements=ParcelMeasurements(weight_grams=1400)),
    )

    assert request.measurements.weight_grams == 1400


def test_a_measurement_of_zero_is_refused_rather_than_stored() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ParcelMeasurements(weight_grams=0)


# ------------------------------------------------------------------ MER-06/07


def test_phone_and_governorate_are_the_only_mandatory_receiver_fields() -> None:
    """v6.3 p.12 — name, address and pin are helpful but not required."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(order_id=order.order_id, draft=draft())

    assert request.receiver.phone == "+9647701234567"
    assert request.receiver.governorate == "BAGHDAD"
    assert request.receiver.name is None
    assert request.receiver.address_line is None


def test_a_local_iraqi_number_is_normalised() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(
        order_id=order.order_id, draft=draft(receiver=receiver(phone="07701234567"))
    )

    assert request.receiver.phone == "+9647701234567"


def test_an_unusable_phone_number_is_refused() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(InvalidPhoneNumber):
        service.add_shipment(
            order_id=order.order_id, draft=draft(receiver=receiver(phone="12"))
        )


def test_an_unknown_governorate_is_refused() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(UnknownGovernorate):
        service.add_shipment(
            order_id=order.order_id, draft=draft(receiver=receiver("ATLANTIS"))
        )


# ------------------------------------------------------------------ MER-05 labels


def test_a_merchant_shipment_waits_for_its_preprinted_label() -> None:
    """v6.3 p.10 — details, then stick a label, then scan it to link the two."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(order_id=order.order_id, draft=draft())

    assert request.status is RequestStatus.AWAITING_LABEL
    assert request.has_label is False


def test_scanning_the_label_links_it_and_makes_the_parcel_collectable() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())

    linked = service.link_label(request_id=request.request_id, label_code="hh-000001")

    assert linked.label_code == "HH-000001"
    assert linked.status is RequestStatus.READY_FOR_PICKUP
    assert linked.label_linked_at is not None


def test_one_label_cannot_be_linked_to_two_parcels() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    first = service.add_shipment(order_id=order.order_id, draft=draft())
    second = service.add_shipment(order_id=order.order_id, draft=draft())
    service.link_label(request_id=first.request_id, label_code="HH-000001")

    with pytest.raises(LabelAlreadyLinked):
        service.link_label(request_id=second.request_id, label_code="HH-000001")


def test_a_customer_never_labels_their_own_parcel() -> None:
    """v6.3 p.18 — hub staff stick the label and scan it at drop-off."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())

    with pytest.raises(LabelNotAllowedForCustomer):
        service.link_label(request_id=request.request_id, label_code="HH-000001")


def test_a_malformed_label_code_is_refused() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())

    with pytest.raises(InvalidLabelCode):
        service.link_label(request_id=request.request_id, label_code="x")


def test_a_label_cannot_be_relinked_once_the_parcel_is_ready() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())
    service.link_label(request_id=request.request_id, label_code="HH-000001")

    with pytest.raises(RequestTransitionNotAllowed):
        service.link_label(request_id=request.request_id, label_code="HH-000002")


# ------------------------------------------------------------------ MER-19 pickup


def test_pickup_opens_only_once_every_parcel_carries_a_label() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    first = service.add_shipment(order_id=order.order_id, draft=draft())
    service.add_shipment(order_id=order.order_id, draft=draft())
    service.link_label(request_id=first.request_id, label_code="HH-000001")

    with pytest.raises(PickupBlockedByMissingLabels) as caught:
        service.book_pickup(
            order_id=order.order_id, window_start=at(9), window_end=at(13)
        )

    assert caught.value.unlabelled == 1


def test_pickup_opens_when_the_last_label_is_attached() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    first = service.add_shipment(order_id=order.order_id, draft=draft())
    second = service.add_shipment(order_id=order.order_id, draft=draft())
    service.link_label(request_id=first.request_id, label_code="HH-000001")
    service.link_label(request_id=second.request_id, label_code="HH-000002")

    booked = service.book_pickup(
        order_id=order.order_id, window_start=at(9), window_end=at(13)
    )

    assert len(booked) == 2
    assert all(request.pickup_window_start == at(9) for request in booked)


def test_a_regular_customer_never_gets_a_pickup() -> None:
    """v6.3 p.8, p.15 — the parcel is handed in at a hub instead."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())
    service.add_shipment(order_id=order.order_id, draft=draft())

    with pytest.raises(PickupNotAvailableForCustomers):
        service.book_pickup(
            order_id=order.order_id, window_start=at(9), window_end=at(13)
        )


def test_readiness_reports_how_many_parcels_still_need_a_label() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    first = service.add_shipment(order_id=order.order_id, draft=draft())
    service.add_shipment(order_id=order.order_id, draft=draft())
    service.link_label(request_id=first.request_id, label_code="HH-000001")

    assert service.order_pickup_readiness(order_id=order.order_id) == (1, 2)


# ------------------------------------------------------------------ MER-18 bulk


def test_a_batch_creates_every_shipment_at_once() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    created = service.add_shipments(
        order_id=order.order_id, drafts=tuple(draft() for _ in range(5))
    )

    assert len(created) == 5
    assert len({request.tracking_code for request in created}) == 5


def test_a_batch_with_one_bad_row_creates_nothing() -> None:
    """Half a batch would leave the merchant guessing which rows to retype."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(DescriptionRequired):
        service.add_shipments(
            order_id=order.order_id,
            drafts=(draft(), draft(description=""), draft()),
        )

    assert service.list_for_order(order.order_id) == ()


# ------------------------------------------------------------------ MER-22/23


def test_a_prohibited_kind_of_goods_is_refused_at_creation() -> None:
    uow = build_store()
    catalogue_service(uow).upsert(
        code="WEAPONS", display_name="Weapons", prohibited=True
    )
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(ProhibitedGoods):
        service.add_shipment(
            order_id=order.order_id, draft=draft(goods_category_code="WEAPONS")
        )


def test_an_unknown_goods_category_is_refused() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(UnknownGoodsCategory):
        service.add_shipment(
            order_id=order.order_id, draft=draft(goods_category_code="MADE_UP")
        )


def test_a_permitted_category_is_recorded() -> None:
    uow = build_store()
    catalogue_service(uow).upsert(
        code="ELECTRONICS", display_name="Electronics", hint="Phones, laptops, chargers"
    )
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(
        order_id=order.order_id, draft=draft(goods_category_code="ELECTRONICS")
    )

    assert request.goods_category_code == "ELECTRONICS"


def test_the_prohibited_rules_must_be_acknowledged_when_required() -> None:
    uow = build_store()
    service = send_service(uow, require_prohibited_acknowledgement=True)
    order = service.open_order(sender=merchant_sender())

    with pytest.raises(ProhibitedGoodsNotAcknowledged):
        service.add_shipment(order_id=order.order_id, draft=draft())


def test_acknowledging_the_rules_lets_the_shipment_through() -> None:
    uow = build_store()
    service = send_service(uow, require_prohibited_acknowledgement=True)
    order = service.open_order(sender=merchant_sender())

    request = service.add_shipment(
        order_id=order.order_id, draft=draft(prohibited_goods_acknowledged=True)
    )

    assert request.status is RequestStatus.AWAITING_LABEL


# ------------------------------------------------------------------ sender model


def test_a_merchant_sender_must_name_its_merchant() -> None:
    with pytest.raises(ValueError, match="must name its merchant"):
        SenderRef(kind=SenderKind.MERCHANT, principal_id=customer_sender().principal_id)


def test_a_customer_sender_cannot_belong_to_a_merchant() -> None:

    with pytest.raises(ValueError, match="cannot belong to a merchant"):
        SenderRef(
            kind=SenderKind.CUSTOMER,
            principal_id=customer_sender().principal_id,
            merchant_id=MERCHANT_ID,
        )


def test_a_cancelled_order_takes_no_new_shipments() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    uow.begin()
    stored = uow.orders.get(order.order_id)
    stored.status = OrderStatus.CANCELLED
    uow.orders.save(stored)
    uow.commit()

    with pytest.raises(OrderNotOpen):
        service.add_shipment(order_id=order.order_id, draft=draft())
