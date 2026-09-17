"""Driver onboarding and the office verification gate (SEC-03)."""

from __future__ import annotations

import pytest
from workforce_fixtures import (
    APPLICANT,
    MONDAY,
    OPERATOR,
    a_draft,
    a_vehicle,
    attendance_service,
    build_store,
    eligibility_service,
    onboarding_service,
    verified_driver,
)

from workforce.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationTransitionNotAllowed,
    DocumentsIncomplete,
    ShiftSelectionRequired,
    TermsNotAccepted,
)
from workforce.domain.value_objects import (
    REQUIRED_OFFICE_DOCUMENTS,
    ApplicationStatus,
    DriverStatus,
)


def test_an_application_starts_pending_office_verification() -> None:
    """Driver App v8 ``reg4`` — "Your account status is now Pending"."""
    uow = build_store()

    application = onboarding_service(uow).submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    assert application.status is ApplicationStatus.AWAITING_OFFICE_VERIFICATION
    assert application.awaits_office_visit is True
    assert application.reference.startswith("APP-")


def test_submitting_remotely_creates_no_driver_at_all() -> None:
    """The gate is that a form alone never produces someone who can be given work."""
    uow = build_store()
    onboarding_service(uow).submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    assert onboarding_service(uow).find_driver(principal_id=APPLICANT) is None


def test_an_unverified_applicant_is_not_assignable() -> None:
    uow = build_store()
    onboarding_service(uow).submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    eligibility = eligibility_service(uow).for_principal(principal_id=APPLICANT)

    assert eligibility.eligible is False
    assert "NO_DRIVER_PROFILE" in eligibility.reasons


def test_an_applicant_must_accept_the_agreement() -> None:
    uow = build_store()

    with pytest.raises(TermsNotAccepted):
        onboarding_service(uow).submit_application(
            applicant_principal_id=APPLICANT, draft=a_draft(terms_version="  ")
        )


def test_an_applicant_must_pick_at_least_one_shift() -> None:
    """Driver App v8 ``regHours``: "Pick at least one shift"."""
    uow = build_store()

    with pytest.raises(ShiftSelectionRequired):
        onboarding_service(uow).submit_application(
            applicant_principal_id=APPLICANT, draft=a_draft(declared_shifts=())
        )


def test_one_open_application_per_applicant() -> None:
    uow = build_store()
    service = onboarding_service(uow)
    first = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    with pytest.raises(ApplicationAlreadyOpen) as caught:
        service.submit_application(applicant_principal_id=APPLICANT, draft=a_draft())

    assert caught.value.reference == first.reference


def test_a_withdrawn_application_frees_the_slot() -> None:
    uow = build_store()
    service = onboarding_service(uow)
    first = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )
    service.withdraw(application_id=first.application_id)

    second = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    assert second.application_id != first.application_id


# ------------------------------------------------------------------ the gate


def test_the_office_visit_is_what_creates_the_driver() -> None:
    uow = build_store()

    driver = verified_driver(uow)

    assert driver.status is DriverStatus.ACTIVE
    assert driver.principal_id == APPLICANT
    assert driver.vehicle.plate_number == "BG 12345"


def test_verification_records_who_checked_and_where() -> None:
    """SEC-09 — the check is attributable to a named operator at a named office."""
    uow = build_store()
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    result = service.record_office_verification(
        application_id=application.application_id,
        operator_principal_id=OPERATOR,
        office="Karbala office",
        documents_verified=REQUIRED_OFFICE_DOCUMENTS,
    )

    assert result.application.verified_by_actor_id == OPERATOR
    assert result.application.verified_at_office == "Karbala office"
    assert result.application.verified_at is not None


def test_an_incomplete_document_check_is_refused() -> None:
    """A partial pass would defeat the whole point of a physical check."""
    uow = build_store()
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    with pytest.raises(DocumentsIncomplete) as caught:
        service.record_office_verification(
            application_id=application.application_id,
            operator_principal_id=OPERATOR,
            office="Karbala office",
            documents_verified=("ID_CARD", "DRIVING_LICENCE"),
        )

    assert set(caught.value.missing) == {
        "VEHICLE_REGISTRATION",
        "INSURANCE_CERTIFICATE",
    }


def test_a_refused_verification_creates_no_driver() -> None:
    uow = build_store()
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    with pytest.raises(DocumentsIncomplete):
        service.record_office_verification(
            application_id=application.application_id,
            operator_principal_id=OPERATOR,
            office="Karbala office",
            documents_verified=("ID_CARD",),
        )

    assert service.find_driver(principal_id=APPLICANT) is None


def test_the_required_document_list_matches_what_the_app_asks_for() -> None:
    """Driver App v8 ``reg4``: ID card, licence, vehicle registration, insurance."""
    assert set(REQUIRED_OFFICE_DOCUMENTS) == {
        "ID_CARD",
        "DRIVING_LICENCE",
        "VEHICLE_REGISTRATION",
        "INSURANCE_CERTIFICATE",
    }


def test_a_verified_application_cannot_be_verified_again() -> None:
    uow = build_store()
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )
    service.record_office_verification(
        application_id=application.application_id,
        operator_principal_id=OPERATOR,
        office="Karbala office",
        documents_verified=REQUIRED_OFFICE_DOCUMENTS,
    )

    with pytest.raises(ApplicationTransitionNotAllowed):
        service.record_office_verification(
            application_id=application.application_id,
            operator_principal_id=OPERATOR,
            office="Karbala office",
            documents_verified=REQUIRED_OFFICE_DOCUMENTS,
        )


def test_a_rejected_application_records_its_reason() -> None:
    uow = build_store()
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=APPLICANT, draft=a_draft()
    )

    rejected = service.reject(
        application_id=application.application_id,
        operator_principal_id=OPERATOR,
        reason="vehicle registration did not match the applicant",
    )

    assert rejected.status is ApplicationStatus.REJECTED
    assert "registration" in rejected.decision_reason


def test_verification_carries_the_declared_shifts_into_a_pattern() -> None:
    uow = build_store()
    driver = verified_driver(uow)

    pattern = attendance_service(uow).current_pattern(
        driver_id=driver.driver_id, day=MONDAY
    )
    assert pattern is not None
    assert len(pattern.windows_for(MONDAY)) == 1


# ------------------------------------------------------------------ vehicle


def test_a_plate_number_is_validated() -> None:
    with pytest.raises(ValueError, match="plate number"):
        a_vehicle(plate_number="!")
