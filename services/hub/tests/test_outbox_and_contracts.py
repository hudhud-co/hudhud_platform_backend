"""Transactional outbox and registered event contracts for Hub."""

from __future__ import annotations

import pytest
from hub_fixtures import (
    OPERATIONS,
    OPERATOR,
    TRACKING,
    accepted_drop_off,
    build_store,
    drop_off_service,
    karbala_hub,
    processing_service,
    sorted_parcel,
    walk_in_details,
)

from hub.domain.errors import DropOffNotLabelled
from hub.domain.messaging import OutboxStatus
from hub.domain.value_objects import HoldReason
from hub.infrastructure.contracts.registry import (
    DROP_OFF_ACCEPTED_EVENT_TYPE,
    PARCEL_HELD_EVENT_TYPE,
    ContractAssetMissing,
    EnvelopeContractValidationFailed,
    load_hub_fact_registry,
    validate_envelope,
)
from hub.infrastructure.nats.subjects import (
    ALLOWED_SUBJECTS,
    STREAM_HUB,
    expected_stream_for_subject,
    validate_subject_allowed,
)

# ------------------------------------------------------------------ outbox


def test_accepting_a_drop_off_writes_its_event_in_the_same_transaction() -> None:
    uow = build_store()
    hub = karbala_hub(uow)

    result = accepted_drop_off(uow, hub)

    record = uow.as_committed().outbox.get_by_event_id(result.event_id)
    assert record is not None
    assert record.status is OutboxStatus.PENDING
    assert record.event_type == DROP_OFF_ACCEPTED_EVENT_TYPE
    assert record.aggregate_id == result.drop_off.drop_off_id


def test_a_refused_acceptance_writes_no_event() -> None:
    """A rolled-back command must leave nothing publishable behind."""
    uow = build_store()
    hub = karbala_hub(uow)
    service = drop_off_service(uow)
    drop_off = service.capture_details(
        hub_id=hub.hub_id, tracking_code=TRACKING, details=walk_in_details()
    )

    with pytest.raises(DropOffNotLabelled):
        service.accept(drop_off_id=drop_off.drop_off_id, operator_principal_id=OPERATOR)

    assert uow.as_committed().outbox.list_pending() == ()


def test_the_acceptance_event_names_the_operator_who_took_custody() -> None:
    """SEC-09 — every custody-changing action is attributable to a proven actor."""
    uow = build_store()
    hub = karbala_hub(uow)

    result = accepted_drop_off(uow, hub)

    payload = uow.as_committed().outbox.get_by_event_id(result.event_id).payload_json["payload"]
    assert payload["accepting_actor_id"] == str(OPERATOR)
    assert payload["label_code"] == "HH-000001"
    assert payload["weight_grams"] == 1400


def test_the_hold_event_carries_the_disposition_already_decided() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)

    outcome = processing_service(uow).report_checkpoint_interception(
        hub_id=hub.hub_id, tracking_code=TRACKING, reported_by_actor_id=OPERATIONS
    )

    record = uow.as_committed().outbox.get_by_event_id(outcome.event_id)
    assert record.event_type == PARCEL_HELD_EVENT_TYPE
    assert record.payload_json["payload"]["disposition"] == "RETURN_TO_MERCHANT"


# ------------------------------------------------------------------ PII


def test_no_hub_event_declares_pii() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    accepted_drop_off(uow, hub)
    for record in uow.as_committed().outbox.list_pending():
        assert record.payload_json["pii_present"] is False


def test_the_receivers_phone_taken_at_the_counter_never_reaches_an_event() -> None:
    """Hub holds those details to route the parcel, not to broadcast them."""
    uow = build_store()
    hub = karbala_hub(uow)
    result = accepted_drop_off(uow, hub)

    rendered = str(uow.as_committed().outbox.get_by_event_id(result.event_id).payload_json)
    assert "7701234567" not in rendered


# ------------------------------------------------------------------ contracts


def test_both_hub_contracts_are_registered() -> None:
    for event_type in (DROP_OFF_ACCEPTED_EVENT_TYPE, PARCEL_HELD_EVENT_TYPE):
        loaded = load_hub_fact_registry(event_type, 1)
        assert loaded.contract.producer == "hub"
        assert loaded.contract.stream == STREAM_HUB


def test_an_unregistered_contract_fails_closed() -> None:
    with pytest.raises(ContractAssetMissing):
        load_hub_fact_registry("hub.fact.invented", 1)


def test_the_contract_fixes_the_disposition_for_a_checkpoint_interception() -> None:
    """v6.3 p.24 Confirmed decision, enforced by the schema as well as the service."""
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)
    outcome = processing_service(uow).report_checkpoint_interception(
        hub_id=hub.hub_id, tracking_code=TRACKING, reported_by_actor_id=OPERATIONS
    )
    envelope = dict(uow.as_committed().outbox.get_by_event_id(outcome.event_id).payload_json)
    payload = dict(envelope["payload"])
    payload["disposition"] = "CONTINUE"
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope, event_type=PARCEL_HELD_EVENT_TYPE, event_version=1
        )


def test_the_contract_forbids_a_tamper_hold_that_simply_continues() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    sorted_parcel(uow, hub)
    outcome = processing_service(uow).hold(
        hub_id=hub.hub_id,
        tracking_code=TRACKING,
        reason=HoldReason.TAMPER_INVESTIGATION,
    )
    envelope = dict(uow.as_committed().outbox.get_by_event_id(outcome.event_id).payload_json)
    payload = dict(envelope["payload"])
    payload["disposition"] = "CONTINUE"
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope, event_type=PARCEL_HELD_EVENT_TYPE, event_version=1
        )


def test_the_contract_requires_a_weight_on_an_acceptance() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    result = accepted_drop_off(uow, hub)
    envelope = dict(uow.as_committed().outbox.get_by_event_id(result.event_id).payload_json)
    payload = dict(envelope["payload"])
    del payload["weight_grams"]
    envelope["payload"] = payload

    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            envelope, event_type=DROP_OFF_ACCEPTED_EVENT_TYPE, event_version=1
        )


# ------------------------------------------------------------------ subjects


def test_only_hub_owned_subjects_may_be_published() -> None:
    for subject in ALLOWED_SUBJECTS:
        validate_subject_allowed(subject)
        assert expected_stream_for_subject(subject) == STREAM_HUB


def test_a_foreign_subject_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_subject_allowed("hudhud.pickup.pickup.fact.accepted.v1")


def test_every_published_subject_is_allowlisted() -> None:
    uow = build_store()
    hub = karbala_hub(uow)
    accepted_drop_off(uow, hub)
    for record in uow.as_committed().outbox.list_pending():
        validate_subject_allowed(record.subject)
