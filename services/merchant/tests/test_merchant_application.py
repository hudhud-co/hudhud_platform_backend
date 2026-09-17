"""Merchant-status application (MER-01) and the MER-02 fail-closed gate."""

from __future__ import annotations

import pytest
from merchant_fixtures import (
    APPLICANT,
    REVIEWER,
    application_service,
    build_store,
    undecided_application_service,
    valid_attributes,
)

from merchant.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationAttributesMissing,
    ApplicationDataSetNotDefined,
    ApplicationTransitionNotAllowed,
    DecisionReasonRequired,
    MerchantCodeAlreadyIssued,
)
from merchant.domain.value_objects import ApplicationStatus, MerchantStatus

# ------------------------------------------------------------------ MER-02


def test_submission_is_refused_while_the_data_set_is_undecided() -> None:
    """The shipped configuration refuses to submit — v6.3 p.10 open item."""
    uow = build_store()
    service = undecided_application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )

    with pytest.raises(ApplicationDataSetNotDefined):
        service.submit(application_id=application.application_id)


def test_the_refusal_names_the_setting_that_unblocks_it() -> None:
    uow = build_store()
    service = undecided_application_service(uow)
    application = service.start_application(applicant_principal_id=APPLICANT)

    with pytest.raises(ApplicationDataSetNotDefined) as caught:
        service.submit(application_id=application.application_id)

    assert "MERCHANT_APPLICATION_REQUIRED_ATTRIBUTES" in str(caught.value)


def test_drafting_still_works_while_the_data_set_is_undecided() -> None:
    """Everything around the blocked operation is built and usable."""
    uow = build_store()
    service = undecided_application_service(uow)
    application = service.start_application(applicant_principal_id=APPLICANT)

    updated = service.update_attributes(
        application_id=application.application_id,
        attributes={"business_name": "Karbala Home Goods"},
    )

    assert updated.attributes["business_name"] == "Karbala Home Goods"
    assert updated.status is ApplicationStatus.DRAFT


def test_a_refused_submission_leaves_the_application_in_draft() -> None:
    uow = build_store()
    service = undecided_application_service(uow)
    application = service.start_application(applicant_principal_id=APPLICANT)

    with pytest.raises(ApplicationDataSetNotDefined):
        service.submit(application_id=application.application_id)

    assert service.get(application.application_id).status is ApplicationStatus.DRAFT


def test_submission_requires_every_configured_attribute() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes={"business_name": "Only this"}
    )

    with pytest.raises(ApplicationAttributesMissing) as caught:
        service.submit(application_id=application.application_id)

    assert set(caught.value.missing) == {"business_type", "business_phone", "city"}


def test_a_blank_attribute_does_not_satisfy_a_requirement() -> None:
    uow = build_store()
    service = application_service(uow)
    attributes = valid_attributes() | {"city": "   "}
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=attributes
    )

    with pytest.raises(ApplicationAttributesMissing) as caught:
        service.submit(application_id=application.application_id)

    assert caught.value.missing == ("city",)


# ------------------------------------------------------------------ lifecycle


def test_one_application_at_a_time() -> None:
    uow = build_store()
    service = application_service(uow)
    first = service.start_application(applicant_principal_id=APPLICANT)

    with pytest.raises(ApplicationAlreadyOpen) as caught:
        service.start_application(applicant_principal_id=APPLICANT)

    assert caught.value.application_id == str(first.application_id)


def test_an_approved_applicant_is_not_blocked_from_a_later_application() -> None:
    """APPROVED is terminal, so it no longer occupies the single open slot."""
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)
    service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    )

    second = service.start_application(applicant_principal_id=APPLICANT)

    assert second.status is ApplicationStatus.DRAFT


def test_an_application_carries_a_quotable_reference() -> None:
    uow = build_store()
    application = application_service(uow).start_application(
        applicant_principal_id=APPLICANT
    )

    assert application.reference.startswith("MAP-")
    assert len(application.reference) == len("MAP-YYYYMMDD-XXXXXX")


def test_approval_creates_the_merchant_its_code_and_its_policy() -> None:
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

    assert result.application.status is ApplicationStatus.APPROVED
    assert result.merchant.status is MerchantStatus.ACTIVE
    assert result.merchant.owner_principal_id == APPLICANT
    assert len(result.merchant.merchant_code) == 8
    assert result.policy.merchant_id == result.merchant.merchant_id


def test_an_approved_merchant_starts_with_every_add_on_off() -> None:
    """v6.3 p.13 open-box off by default; p.14 no photo without the add-on."""
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)

    policy = service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    ).policy

    assert policy.open_box_allowed is False
    assert policy.photo_documentation_enabled is False
    assert policy.hudhud_packaging_enabled is False


def test_changes_requested_keeps_the_applicant_a_regular_customer() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)

    decided = service.request_changes(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        reason="address could not be confirmed",
    )

    assert decided.status is ApplicationStatus.CHANGES_REQUESTED
    assert decided.decision_reason == "address could not be confirmed"
    assert uow.merchants.find_by_owner(APPLICANT) is None


def test_changes_requested_requires_a_reason() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)

    with pytest.raises(DecisionReasonRequired):
        service.request_changes(
            application_id=application.application_id,
            reviewer_principal_id=REVIEWER,
            reason="   ",
        )


def test_answers_are_kept_for_an_edit_and_resubmit() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)
    service.request_changes(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        reason="address could not be confirmed",
    )

    edited = service.update_attributes(
        application_id=application.application_id,
        attributes={"city": "NAJAF"},
    )
    resubmitted = service.submit(application_id=application.application_id)

    assert edited.attributes["business_name"] == "Karbala Home Goods"
    assert resubmitted.status is ApplicationStatus.SUBMITTED
    assert resubmitted.decision_reason is None


def test_an_approved_application_cannot_be_decided_again() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)
    service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    )

    with pytest.raises(ApplicationTransitionNotAllowed):
        service.request_changes(
            application_id=application.application_id,
            reviewer_principal_id=REVIEWER,
            reason="second thoughts",
        )


def test_a_draft_cannot_be_approved_without_review() -> None:
    uow = build_store()
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )

    with pytest.raises(ApplicationTransitionNotAllowed):
        service.approve(
            application_id=application.application_id,
            reviewer_principal_id=REVIEWER,
            display_name="Karbala Home Goods",
        )


def test_a_withdrawn_application_frees_the_slot() -> None:
    uow = build_store()
    service = application_service(uow)
    first = service.start_application(applicant_principal_id=APPLICANT)
    service.withdraw(application_id=first.application_id)

    second = service.start_application(applicant_principal_id=APPLICANT)

    assert second.application_id != first.application_id


def test_a_requested_merchant_code_cannot_collide() -> None:
    uow = build_store()
    service = application_service(uow)
    first = service.start_application(
        applicant_principal_id=APPLICANT, attributes=valid_attributes()
    )
    service.submit(application_id=first.application_id)
    service.approve(
        application_id=first.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="First",
        merchant_code="KARBALA1",
    )

    second_applicant = REVIEWER
    second = service.start_application(
        applicant_principal_id=second_applicant, attributes=valid_attributes()
    )
    service.submit(application_id=second.application_id)

    with pytest.raises(MerchantCodeAlreadyIssued):
        service.approve(
            application_id=second.application_id,
            reviewer_principal_id=REVIEWER,
            display_name="Second",
            merchant_code="karbala1",
        )
