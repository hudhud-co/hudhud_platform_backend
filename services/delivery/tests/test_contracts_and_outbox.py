"""Event contracts and the transactional outbox.

Every fact Delivery publishes is validated against its registered schema before it
reaches the outbox, and every fact is written in the same transaction as the state change
that caused it. The PII guard is the third leg: a consumer that needs a receiver's name
or a delivery code asks Delivery's API and is authorized for it — the bus never carries
either.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from delivery_fixtures import authorize_at_the_door, build_lab, iqd, scan_parcel

from delivery.domain.errors import PaymentRequiredBeforeHandover
from delivery.domain.messaging import OutboxStatus
from delivery.domain.value_objects import InspectionOutcome, PaymentMethod
from delivery.infrastructure.contracts.envelopes import (
    build_cod_collected_envelope,
    build_delivered_envelope,
)
from delivery.infrastructure.contracts.registry import (
    DELIVERY_FACT_CONTRACTS,
    EnvelopeContractValidationFailed,
    load_delivery_fact_registry,
    validate_envelope,
)
from delivery.infrastructure.nats.subjects import (
    ALLOWED_SUBJECTS,
    expected_stream_for_subject,
    validate_subject_allowed,
)

# --------------------------------------------------------------- the registry


@pytest.mark.parametrize(("event_type", "event_version"), DELIVERY_FACT_CONTRACTS)
def test_every_declared_contract_resolves(event_type: str, event_version: int) -> None:
    loaded = load_delivery_fact_registry(event_type, event_version)
    assert loaded.contract.producer == "delivery"
    assert loaded.contract.subject in ALLOWED_SUBJECTS


def test_the_subject_allowlist_matches_the_registry() -> None:
    """A subject that is allowlisted but unregistered would publish an unvalidated fact."""
    registered = {
        load_delivery_fact_registry(event_type, version).contract.subject
        for event_type, version in DELIVERY_FACT_CONTRACTS
    }
    assert registered == set(ALLOWED_SUBJECTS)


def test_every_subject_binds_to_the_delivery_stream() -> None:
    for subject in ALLOWED_SUBJECTS:
        assert expected_stream_for_subject(subject) == "HUDHUD_DELIVERY"


def test_an_unlisted_subject_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_subject_allowed("hudhud.delivery.something.else.v1")


def test_a_malformed_envelope_is_refused_before_it_can_be_published() -> None:
    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            {"event_type": "delivery.fact.delivered"},
            event_type="delivery.fact.delivered",
            event_version=1,
        )


# ------------------------------------------------------------------ the facts


def test_a_delivery_writes_its_fact_in_the_same_transaction() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    before = lab.uow.commits
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    record = lab.uow.outbox.get_by_event_id(result.delivered_event_id)
    assert record is not None
    assert record.status is OutboxStatus.PENDING
    assert record.aggregate_id == stop.stop_id
    # One commit covers both the transition and the fact.
    assert lab.uow.commits == before + 1


def test_a_failed_handover_writes_no_fact() -> None:
    """A COD parcel refused for non-payment must not announce a delivery."""
    lab = build_lab()
    stop = scan_parcel(
        lab, cod_amount=iqd(25_000), payment_method_expected=PaymentMethod.CASH
    )
    authorize_at_the_door(lab, stop)
    with pytest.raises(PaymentRequiredBeforeHandover):
        lab.outcomes.complete_delivery(
            stop_id=stop.stop_id, driver_principal_id=lab.driver_id
        )
    # The departure fact published on the way here stands; no handover was announced.
    types = {r.event_type for r in lab.uow.outbox.list_for_aggregate(stop.stop_id)}
    assert types == {"delivery.fact.departed_for_receiver"}


def test_the_departure_fact_carries_a_window_not_a_time() -> None:
    """DRV-L01 — the receiver is given an ETA, never a promised minute."""
    lab = build_lab()
    stop = scan_parcel(lab)
    event_id = lab.outcomes.announce_departure(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        eta_from_minutes=20,
        eta_to_minutes=40,
    )
    record = lab.uow.outbox.get_by_event_id(event_id)
    payload = record.payload_json["payload"]
    assert payload["eta_window_minutes_from"] == 20
    assert payload["eta_window_minutes_to"] == 40


def test_the_cod_fact_says_card_money_is_not_in_driver_custody() -> None:
    lab = build_lab()
    stop = scan_parcel(
        lab, cod_amount=iqd(25_000), payment_method_expected=PaymentMethod.POS_CARD
    )
    authorize_at_the_door(lab, stop)
    lab.payments.record_card_approval(
        stop_id=stop.stop_id,
        driver_principal_id=lab.driver_id,
        pos_reference="POS-84213",
    )
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    record = lab.uow.outbox.get_by_event_id(result.cod_event_id)
    assert record.payload_json["payload"]["enters_driver_cash_custody"] is False


def test_the_cod_fact_says_cash_is_in_driver_custody() -> None:
    lab = build_lab()
    stop = scan_parcel(
        lab, cod_amount=iqd(25_000), payment_method_expected=PaymentMethod.CASH
    )
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    record = lab.uow.outbox.get_by_event_id(result.cod_event_id)
    payload = record.payload_json["payload"]
    assert payload["enters_driver_cash_custody"] is True
    assert payload["amount_minor_units"] == 25_000
    assert payload["currency"] == "IQD"


def test_money_crosses_the_bus_as_integer_minor_units() -> None:
    lab = build_lab()
    stop = scan_parcel(
        lab, cod_amount=iqd(25_000), payment_method_expected=PaymentMethod.CASH
    )
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    result = lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    for record in lab.uow.outbox.list_for_aggregate(stop.stop_id):
        for key, value in record.payload_json["payload"].items():
            if key.endswith("minor_units") and value is not None:
                assert isinstance(value, int), key
                assert not isinstance(value, float), key
    assert result.cod_event_id is not None


def test_a_refusal_is_not_published_as_a_delivery() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.outcomes.record_refusal(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    types = {r.event_type for r in lab.uow.outbox.list_for_aggregate(stop.stop_id)}
    assert "delivery.fact.delivered" not in types
    assert "delivery.fact.attempt_failed" in types


def test_the_builder_refuses_to_call_a_refusal_a_delivery() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    refused = lab.outcomes.record_refusal(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    ).stop
    refused.inspection_outcome = InspectionOutcome.OPEN_BOX_REFUSED
    # Everything else about the stop is made to look like a delivery, so the only
    # thing that can refuse the envelope is the refusal itself.
    refused.delivered_at = refused.closed_at
    with pytest.raises(ValueError, match="a refusal is not a delivery"):
        build_delivered_envelope(
            stop=refused,
            payment=None,
            photo_count=0,
            aggregate_version=1,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


def test_the_builder_refuses_a_prepaid_cod_fact() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    result = lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    # Given an amount, so the refusal is about the method and not the missing figure.
    prepaid = result.payment
    prepaid.amount = iqd(25_000)
    with pytest.raises(ValueError, match="collects nothing at the door"):
        build_cod_collected_envelope(
            stop=stop,
            payment=prepaid,
            aggregate_version=1,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


# ------------------------------------------------------------------ no PII


def test_no_published_fact_carries_pii_or_a_code() -> None:
    """The bus carries facts, not people.

    A receiver's name, a landmark, a phone number and the delivery code are all absent
    by construction; this walks the real payloads rather than trusting the builders.
    """
    lab = build_lab()
    stop = scan_parcel(
        lab,
        named_receiver="Zaid Al-Rawi",
        cod_amount=iqd(25_000),
        payment_method_expected=PaymentMethod.CASH,
    )
    lab.receiver.set_handover_preference(
        tracking_code=stop.tracking_code, landmark="Behind the blue mosque"
    )
    authorize_at_the_door(lab, stop)
    lab.payments.collect_cash(stop_id=stop.stop_id, driver_principal_id=lab.driver_id)
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )

    forbidden = ("Zaid Al-Rawi", "Behind the blue mosque", "482913")
    for record in lab.uow.outbox.list_for_aggregate(stop.stop_id):
        blob = json.dumps(record.payload_json)
        for secret in forbidden:
            assert secret not in blob, record.event_type


def test_every_fact_declares_it_carries_no_pii() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    for record in lab.uow.outbox.list_for_aggregate(stop.stop_id):
        assert record.payload_json["pii_present"] is False


def test_no_payload_field_is_named_after_a_person() -> None:
    lab = build_lab()
    stop = scan_parcel(lab, named_receiver="Zaid Al-Rawi")
    authorize_at_the_door(lab, stop)
    lab.payments.confirm_prepaid(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    lab.outcomes.complete_delivery(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    for record in lab.uow.outbox.list_for_aggregate(stop.stop_id):
        for key in record.payload_json["payload"]:
            assert "receiver_name" not in key
            assert "phone" not in key
            assert "address" not in key
            assert not (key.endswith("code") and key != "tracking_code")


# ------------------------------------------------------------- the outbox itself


def test_an_event_id_is_never_reused() -> None:
    lab = build_lab()
    first = scan_parcel(lab)
    second = scan_parcel(lab)
    a = lab.outcomes.announce_departure(
        stop_id=first.stop_id, driver_principal_id=lab.driver_id
    )
    b = lab.outcomes.announce_departure(
        stop_id=second.stop_id, driver_principal_id=lab.driver_id
    )
    assert a != b


def test_records_carry_a_retry_budget() -> None:
    lab = build_lab()
    stop = scan_parcel(lab)
    event_id = lab.outcomes.announce_departure(
        stop_id=stop.stop_id, driver_principal_id=lab.driver_id
    )
    record = lab.uow.outbox.get_by_event_id(event_id)
    assert record.max_attempts == 5
    assert record.attempt_count == 0
    assert record.published_at is None
