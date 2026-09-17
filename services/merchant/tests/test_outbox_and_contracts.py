"""Transactional outbox and registered event contracts for Merchant."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from merchant_fixtures import (
    APPLICANT,
    MEMBER,
    REVIEWER,
    a_store,
    an_invite,
    application_service,
    approved_merchant,
    build_store,
    team_service,
    undecided_application_service,
    valid_attributes,
)

from merchant.domain.entities import MerchantApplication
from merchant.domain.errors import ApplicationDataSetNotDefined
from merchant.domain.messaging import OutboxStatus
from merchant.domain.value_objects import ApplicationStatus, MembershipStatus
from merchant.infrastructure.contracts.envelopes import (
    build_application_decided_envelope,
)
from merchant.infrastructure.contracts.registry import (
    APPLICATION_DECIDED_EVENT_TYPE,
    TEAM_MEMBERSHIP_CHANGED_EVENT_TYPE,
    ContractAssetMissing,
    EnvelopeContractValidationFailed,
    load_merchant_fact_registry,
    validate_envelope,
)
from merchant.infrastructure.nats.subjects import (
    ALLOWED_SUBJECTS,
    STREAM_MERCHANT,
    expected_stream_for_subject,
    validate_subject_allowed,
)

# ------------------------------------------------------------------ outbox


def test_an_approval_writes_its_event_in_the_same_transaction() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)

    result = service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    )

    record = uow.outbox.get_by_event_id(result.event_id)
    assert record is not None
    assert record.status is OutboxStatus.PENDING
    assert record.event_type == APPLICATION_DECIDED_EVENT_TYPE
    assert record.aggregate_id == application.application_id


def test_a_refused_submission_writes_no_event() -> None:
    """A rolled-back command must leave nothing publishable behind."""


    uow = build_store()
    service = undecided_application_service(uow)
    application = service.start_application(applicant_principal_id=APPLICANT)

    with pytest.raises(ApplicationDataSetNotDefined):
        service.submit(application_id=application.application_id)

    assert uow.outbox.list_pending() == ()


def test_an_approval_event_names_the_merchant_it_created() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)

    result = service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    )

    payload = uow.outbox.get_by_event_id(result.event_id).payload_json["payload"]
    assert payload["decision"] == "APPROVED"
    assert payload["merchant_id"] == str(result.merchant.merchant_id)
    assert payload["merchant_code"] == result.merchant.merchant_code


def test_a_changes_requested_event_carries_no_reason_text() -> None:
    """The reason is free text a reviewer typed — it stays behind Merchant's API."""
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)
    service.request_changes(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        reason="our courier could not find the shopfront at Al-Nuqabat street 8",
    )

    published = uow.outbox.list_for_aggregate(application.application_id)
    payload = published[0].payload_json["payload"]
    assert payload["decision"] == "CHANGES_REQUESTED"
    assert "reason" not in payload
    assert "Al-Nuqabat" not in str(published[0].payload_json)


def test_no_merchant_event_declares_pii() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    team_service(uow).invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    for record in uow.outbox.list_pending():
        assert record.payload_json["pii_present"] is False


def test_an_invited_phone_number_never_reaches_the_event() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)

    team_service(uow).invite(
        merchant_id=merchant.merchant_id,
        draft=an_invite((store.store_id,), phone="+9647701234567"),
    )

    for record in uow.outbox.list_pending():
        assert "7701234567" not in str(record.payload_json)


def test_membership_changes_advance_the_merchant_aggregate_version() -> None:
    """Two changes to one merchant must never claim the same aggregate version."""
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)
    service.remove(membership_id=membership.membership_id)

    versions = [
        record.aggregate_version
        for record in uow.outbox.list_for_aggregate(merchant.merchant_id)
    ]
    assert len(versions) == len(set(versions)) == 3


def test_an_active_membership_event_names_its_principal() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)

    records = uow.outbox.list_for_aggregate(merchant.merchant_id)
    active = [
        record
        for record in records
        if record.payload_json["payload"]["status"] == MembershipStatus.ACTIVE.value
    ]
    assert active[0].payload_json["payload"]["member_principal_id"] == str(MEMBER)


# ------------------------------------------------------------------ contracts


def test_both_merchant_contracts_are_registered() -> None:
    for event_type in (
        APPLICATION_DECIDED_EVENT_TYPE,
        TEAM_MEMBERSHIP_CHANGED_EVENT_TYPE,
    ):
        loaded = load_merchant_fact_registry(event_type, 1)
        assert loaded.contract.producer == "merchant"
        assert loaded.contract.stream == STREAM_MERCHANT


def test_an_unregistered_contract_fails_closed() -> None:
    with pytest.raises(ContractAssetMissing):
        load_merchant_fact_registry("merchant.fact.invented", 1)


def test_an_envelope_missing_its_payload_is_rejected() -> None:
    with pytest.raises(EnvelopeContractValidationFailed):
        validate_envelope(
            {"envelope_version": 1, "event_type": APPLICATION_DECIDED_EVENT_TYPE},
            event_type=APPLICATION_DECIDED_EVENT_TYPE,
            event_version=1,
        )


def test_an_approval_envelope_without_a_merchant_is_refused_before_the_outbox() -> None:


    application = MerchantApplication(
        application_id=uuid4(),
        applicant_principal_id=APPLICANT,
        reference="MAP-20260914-ABCDEF",
        status=ApplicationStatus.APPROVED,
        decided_at=datetime(2026, 9, 14, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="must name the merchant"):
        build_application_decided_envelope(
            application=application,
            aggregate_version=2,
            event_id=uuid4(),
            correlation_id=uuid4(),
        )


# ------------------------------------------------------------------ subjects


def test_only_merchant_owned_subjects_may_be_published() -> None:
    for subject in ALLOWED_SUBJECTS:
        validate_subject_allowed(subject)
        assert expected_stream_for_subject(subject) == STREAM_MERCHANT


def test_a_foreign_subject_is_refused() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_subject_allowed("hudhud.pickup.pickup.fact.accepted.v1")


def test_every_published_subject_is_allowlisted() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    team_service(uow).invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    for record in uow.outbox.list_pending():
        validate_subject_allowed(record.subject)
