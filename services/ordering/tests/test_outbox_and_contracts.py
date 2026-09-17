"""Transactional outbox and registered event contracts for Ordering."""

from __future__ import annotations

import pytest
from ordering_fixtures import (
    amendment_service,
    build_store,
    cod_draft,
    customer_sender,
    draft,
    labelled_merchant_shipment,
    merchant_sender,
    receiver,
    send_service,
)

from ordering.domain.errors import RequestTransitionNotAllowed
from ordering.domain.messaging import OutboxStatus
from ordering.domain.value_objects import CancellationReason
from ordering.infrastructure.contracts.registry import (
    SHIPMENT_CANCELLED_EVENT_TYPE,
    SHIPMENT_REGISTERED_EVENT_TYPE,
    ContractAssetMissing,
    EnvelopeContractValidationFailed,
    load_ordering_fact_registry,
    validate_envelope,
)
from ordering.infrastructure.nats.subjects import (
    ALLOWED_SUBJECTS,
    STREAM_ORDER,
    expected_stream_for_subject,
    validate_subject_allowed,
)


def _registered(uow, **draft_kwargs):
    service = send_service(uow)
    request = labelled_merchant_shipment(uow, **draft_kwargs)
    return service.register(request_id=request.request_id)


# ------------------------------------------------------------------ outbox


def test_registering_writes_its_event_in_the_same_transaction() -> None:
    uow = build_store()
    request, event_id = _registered(uow)

    record = uow.outbox.get_by_event_id(event_id)
    assert record is not None
    assert record.status is OutboxStatus.PENDING
    assert record.event_type == SHIPMENT_REGISTERED_EVENT_TYPE
    assert record.aggregate_id == request.request_id


def test_a_refused_registration_writes_no_event() -> None:
    """A rolled-back command must leave nothing publishable behind."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())

    # AWAITING_LABEL cannot go straight to REGISTERED.
    with pytest.raises(RequestTransitionNotAllowed):
        service.register(request_id=request.request_id)

    assert uow.outbox.list_pending() == ()


def test_a_customer_registration_says_it_needs_no_pickup() -> None:
    """v6.3 p.8 — regular customers do not get a pickup."""
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=customer_sender())
    request = service.add_shipment(order_id=order.order_id, draft=draft())
    _, event_id = service.register(request_id=request.request_id)

    payload = uow.outbox.get_by_event_id(event_id).payload_json["payload"]
    assert payload["requires_pickup"] is False
    assert payload["sender_kind"] == "CUSTOMER"
    assert payload["merchant_id"] is None
    assert payload["label_code"] is None


def test_a_merchant_registration_carries_its_label_and_needs_a_pickup() -> None:
    uow = build_store()
    _, event_id = _registered(uow)

    payload = uow.outbox.get_by_event_id(event_id).payload_json["payload"]
    assert payload["requires_pickup"] is True
    assert payload["label_code"] == "HH-000001"


def test_a_cod_registration_carries_exact_minor_units() -> None:
    uow = build_store()
    service = send_service(uow)
    order = service.open_order(sender=merchant_sender())
    request = service.add_shipment(order_id=order.order_id, draft=cod_draft(450_000))
    service.link_label(request_id=request.request_id, label_code="HH-000009")
    _, event_id = service.register(request_id=request.request_id)

    payload = uow.outbox.get_by_event_id(event_id).payload_json["payload"]
    assert payload["cod_amount_minor_units"] == 450_000
    assert payload["cod_currency"] == "IQD"
    assert isinstance(payload["cod_amount_minor_units"], int)


# ------------------------------------------------------------------ PII


def test_no_ordering_event_declares_pii() -> None:
    uow = build_store()
    _registered(uow)
    for record in uow.outbox.list_pending():
        assert record.payload_json["pii_present"] is False


def test_a_receivers_phone_name_and_address_never_reach_an_event() -> None:
    """Routing needs the governorate; everything else stays behind Ordering's API."""
    uow = build_store()
    _, event_id = _registered(
        uow,
        receiver=receiver(
            phone="+9647709998888",
            name="Zahra Hussein",
            address_line="House 14, near the bakery",
        ),
    )

    rendered = str(uow.outbox.get_by_event_id(event_id).payload_json)
    assert "9998888" not in rendered
    assert "Zahra" not in rendered
    assert "bakery" not in rendered
    assert "BAGHDAD" in rendered


def test_a_cancellation_event_carries_no_receiver_detail() -> None:
    uow = build_store()
    request = labelled_merchant_shipment(
        uow, receiver=receiver(name="Zahra Hussein", phone="+9647709998888")
    )
    _, event_id = amendment_service(uow).cancel(
        request_id=request.request_id, reason=CancellationReason.DUPLICATE
    )

    rendered = str(uow.outbox.get_by_event_id(event_id).payload_json)
    assert "Zahra" not in rendered
    assert "9998888" not in rendered


# ------------------------------------------------------------------ contracts


def test_both_ordering_contracts_are_registered() -> None:
    for event_type in (SHIPMENT_REGISTERED_EVENT_TYPE, SHIPMENT_CANCELLED_EVENT_TYPE):
        loaded = load_ordering_fact_registry(event_type, 1)
        assert loaded.contract.producer == "ordering"
        assert loaded.contract.stream == STREAM_ORDER


def test_an_unregistered_contract_fails_closed() -> None:
    with pytest.raises(ContractAssetMissing):
        load_ordering_fact_registry("order.fact.invented", 1)


def test_an_envelope_missing_its_payload_is_rejected() -> None:
    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            {"envelope_version": 1, "event_type": SHIPMENT_REGISTERED_EVENT_TYPE},
            event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
            event_version=1,
        )


def test_the_contract_itself_forbids_cod_on_a_customer_parcel() -> None:
    """The schema is the second line of defence behind the domain rule."""
    uow = build_store()
    _, event_id = _registered(uow)
    envelope = dict(uow.outbox.get_by_event_id(event_id).payload_json)
    payload = dict(envelope["payload"])
    payload["sender_kind"] = "CUSTOMER"
    payload["merchant_id"] = None
    payload["payment_terms"] = "CASH_ON_DELIVERY"
    payload["cod_amount_minor_units"] = 450_000
    payload["cod_currency"] = "IQD"
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope,
            event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
            event_version=1,
        )


def test_the_contract_forbids_a_customer_parcel_requiring_a_pickup() -> None:
    uow = build_store()
    _, event_id = _registered(uow)
    envelope = dict(uow.outbox.get_by_event_id(event_id).payload_json)
    payload = dict(envelope["payload"])
    payload["sender_kind"] = "CUSTOMER"
    payload["merchant_id"] = None
    payload["requires_pickup"] = True
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope,
            event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
            event_version=1,
        )


def test_the_contract_requires_an_amount_on_a_cod_registration() -> None:
    uow = build_store()
    _, event_id = _registered(uow)
    envelope = dict(uow.outbox.get_by_event_id(event_id).payload_json)
    payload = dict(envelope["payload"])
    payload["payment_terms"] = "CASH_ON_DELIVERY"
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope,
            event_type=SHIPMENT_REGISTERED_EVENT_TYPE,
            event_version=1,
        )


# ------------------------------------------------------------------ subjects


def test_only_ordering_owned_subjects_may_be_published() -> None:
    for subject in ALLOWED_SUBJECTS:
        validate_subject_allowed(subject)
        assert expected_stream_for_subject(subject) == STREAM_ORDER


def test_a_foreign_subject_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_subject_allowed("hudhud.pickup.pickup.fact.accepted.v1")


def test_every_published_subject_is_allowlisted() -> None:
    uow = build_store()
    _registered(uow)
    for record in uow.outbox.list_pending():
        validate_subject_allowed(record.subject)
