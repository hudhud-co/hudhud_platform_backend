"""Unauthenticated tracking-code lookup (CUS-08).

This is the one endpoint a stranger can reach, so most of these tests are about what it
refuses to say rather than what it returns.
"""

from __future__ import annotations

from dataclasses import fields

from ordering_fixtures import (
    amendment_service,
    build_store,
    cod_draft,
    draft,
    labelled_merchant_shipment,
    merchant_sender,
    receiver,
    send_service,
    tracking_service,
)

from ordering.application.public_tracking_service import PublicTrackingView
from ordering.domain.value_objects import CancellationReason


def test_a_valid_code_returns_the_public_view() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    view = tracking_service(uow).lookup(request.tracking_code)

    assert view is not None
    assert view.tracking_code == request.tracking_code
    assert view.destination_governorate == "BAGHDAD"


def test_the_code_is_accepted_in_any_case_and_with_whitespace() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(uow)

    assert tracking_service(uow).lookup(f"  {request.tracking_code.lower()}  ") is not None


def test_an_unknown_code_returns_nothing() -> None:
    uow = build_store()
    assert tracking_service(uow).lookup("SHP-20260914-000000") is None


def test_a_malformed_code_returns_nothing_rather_than_erroring() -> None:
    uow = build_store()
    for candidate in ("", "hello", "SHP-1-1", "' OR 1=1 --"):
        assert tracking_service(uow).lookup(candidate) is None


def test_a_cancelled_shipment_is_indistinguishable_from_an_unknown_code() -> None:
    """Otherwise the endpoint confirms which codes were ever issued."""
    uow = build_store()
    request = labelled_merchant_shipment(uow)
    amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.SENDER_CHANGED_MIND
    )

    assert tracking_service(uow).lookup(request.tracking_code) is None
    assert tracking_service(uow).lookup("SHP-20260914-000000") is None


# ------------------------------------------------------------------ disclosure


def test_the_public_view_carries_no_personal_data_at_all() -> None:
    """The set of fields is the security boundary, so assert the set itself."""
    exposed = {field.name for field in fields(PublicTrackingView)}

    assert exposed == {
        "tracking_code",
        "status",
        "destination_governorate",
        "created_at",
        "registered_at",
    }


def test_a_receivers_phone_name_and_address_never_appear() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(
        order_id=order.order_id,
        draft=cod_draft(
            receiver=receiver(
                phone="+9647709998888",
                name="Zahra Hussein",
                address_line="House 14, near the bakery",
            )
        ),
    )
    service.link_label(request_id=request.request_id, label_code="HH-000001")

    rendered = str(tracking_service(uow).lookup(request.tracking_code))

    assert "9998888" not in rendered
    assert "Zahra" not in rendered
    assert "bakery" not in rendered


def test_a_cod_amount_never_appears() -> None:
    """A stranger with a tracking code must not learn what the receiver owes."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=cod_draft(amount=450_000))
    service.link_label(request_id=request.request_id, label_code="HH-000001")

    rendered = str(tracking_service(uow).lookup(request.tracking_code))

    assert "450000" not in rendered
    assert "450,000" not in rendered


def test_the_label_code_never_appears() -> None:
    """Knowing the label code would let someone forge the scan the driver performs."""
    uow = build_store()
    request = labelled_merchant_shipment(uow, label="HH-000777")

    assert "000777" not in str(tracking_service(uow).lookup(request.tracking_code))


def test_tracking_codes_are_not_sequential() -> None:
    """A sequential code would let anyone walk the whole network from one link."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    codes = [
        service.add_shipment(order_id=order.order_id, draft=draft()).tracking_code
        for _ in range(8)
    ]
    serials = [int(code.rsplit("-", 1)[1]) for code in codes]

    assert len(set(serials)) == len(serials)
    assert serials != sorted(serials) or max(serials) - min(serials) > len(serials)
