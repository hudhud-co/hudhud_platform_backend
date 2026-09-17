"""Driver onboarding and the office verification gate (SEC-03).

Driver App v8 ``reg4`` states the rule this service exists to enforce: after submitting,
"Your account status is now Pending. You must visit the nearest HUDHUD office to complete
physical verification of your identity, vehicle and documents **before tasks can be
assigned**."

A remote submission therefore never produces an assignable driver. Only an operator
recording an office visit does, and the document checklist must be complete when they do.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from workforce.domain.entities import (
    DriverApplication,
    DriverProfile,
    ShiftPattern,
)
from workforce.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationNotFound,
    ApplicationTransitionNotAllowed,
    DocumentsIncomplete,
    DriverNotFound,
    ShiftSelectionRequired,
    TermsNotAccepted,
)
from workforce.domain.value_objects import (
    REQUIRED_OFFICE_DOCUMENTS,
    ApplicationStatus,
    DriverStatus,
    ShiftWindow,
    VehicleDetails,
)
from workforce.ports.repository import WorkforceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _reference(prefix: str, moment: datetime) -> str:
    return f"{prefix}-{moment:%Y}-{secrets.randbelow(10_000):04d}"


@dataclass(frozen=True, slots=True)
class ApplicationDraft:
    full_name: str
    vehicle: VehicleDetails
    declared_shifts: tuple[ShiftWindow, ...]
    terms_version: str
    documents_received: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerificationResult:
    application: DriverApplication
    driver: DriverProfile
    pattern: ShiftPattern


class OnboardingService:
    def __init__(self, unit_of_work: WorkforceUnitOfWork) -> None:
        self._uow = unit_of_work

    # ------------------------------------------------------------- applicant

    def submit_application(
        self, *, applicant_principal_id: UUID, draft: ApplicationDraft
    ) -> DriverApplication:
        """Submit remotely. This does **not** make anyone assignable."""
        if not draft.terms_version.strip():
            raise TermsNotAccepted()
        if not draft.declared_shifts:
            raise ShiftSelectionRequired()

        self._uow.begin()
        try:
            existing = self._uow.applications.find_open_for_principal(
                applicant_principal_id
            )
            if existing is not None:
                raise ApplicationAlreadyOpen(existing.reference)
            moment = _now()
            application = DriverApplication(
                application_id=uuid4(),
                applicant_principal_id=applicant_principal_id,
                reference=_reference("APP", moment),
                # Straight to the office queue: the app shows "Pending verification" the
                # moment the form is submitted.
                status=ApplicationStatus.AWAITING_OFFICE_VERIFICATION,
                full_name=draft.full_name.strip(),
                vehicle=draft.vehicle,
                declared_shifts=draft.declared_shifts,
                terms_version_accepted=draft.terms_version,
                documents_received=draft.documents_received,
                submitted_at=moment,
            )
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def withdraw(self, *, application_id: UUID) -> DriverApplication:
        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.WITHDRAWN):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.WITHDRAWN.value
                )
            application.status = ApplicationStatus.WITHDRAWN
            application.version += 1
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    # ------------------------------------------------------------- office

    def record_office_verification(
        self,
        *,
        application_id: UUID,
        operator_principal_id: UUID,
        office: str,
        documents_verified: tuple[str, ...],
        effective_from: date | None = None,
    ) -> VerificationResult:
        """An operator confirms the documents in person, and the driver becomes real.

        The checklist is the one the app tells the applicant to bring. An incomplete
        check is refused rather than recorded as a partial pass, because the whole point
        of the gate is that it happened.
        """
        verified = tuple(sorted({item.strip().upper() for item in documents_verified}))
        missing = tuple(
            document for document in REQUIRED_OFFICE_DOCUMENTS if document not in verified
        )
        if missing:
            raise DocumentsIncomplete(missing)

        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.VERIFIED):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.VERIFIED.value
                )
            moment = _now()
            application.status = ApplicationStatus.VERIFIED
            application.verified_at = moment
            application.verified_by_actor_id = operator_principal_id
            application.verified_at_office = office.strip()
            application.documents_verified = verified
            application.version += 1
            self._uow.applications.save(application)

            driver = DriverProfile(
                driver_id=uuid4(),
                principal_id=application.applicant_principal_id,
                application_id=application.application_id,
                full_name=application.full_name,
                vehicle=application.vehicle,
                status=DriverStatus.ACTIVE,
                activated_at=moment,
            )
            self._uow.drivers.save(driver)

            # The shifts declared on the application become the driver's first pattern,
            # effective immediately: there is no earlier pattern for it to disturb.
            pattern = ShiftPattern(
                pattern_id=uuid4(),
                driver_id=driver.driver_id,
                windows=application.declared_shifts,
                effective_from=effective_from or moment.date(),
            )
            self._uow.patterns.save(pattern)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return VerificationResult(
            application=application, driver=driver, pattern=pattern
        )

    def reject(
        self, *, application_id: UUID, operator_principal_id: UUID, reason: str
    ) -> DriverApplication:
        self._uow.begin()
        try:
            application = self._load(application_id)
            if not application.can_transition_to(ApplicationStatus.REJECTED):
                raise ApplicationTransitionNotAllowed(
                    application.status.value, ApplicationStatus.REJECTED.value
                )
            application.status = ApplicationStatus.REJECTED
            application.decision_reason = reason.strip() or None
            application.verified_by_actor_id = operator_principal_id
            application.version += 1
            self._uow.applications.save(application)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    # ------------------------------------------------------------- driver state

    def set_driver_status(self, *, driver_id: UUID, status: DriverStatus) -> DriverProfile:
        self._uow.begin()
        try:
            driver = self._uow.drivers.get(driver_id)
            if driver is None:
                raise DriverNotFound(str(driver_id))
            driver.status = status
            driver.version += 1
            self._uow.drivers.save(driver)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return driver

    # ------------------------------------------------------------- queries

    def get_application(self, application_id: UUID) -> DriverApplication:
        self._uow.begin()
        try:
            application = self._load(application_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return application

    def find_driver(self, *, principal_id: UUID) -> DriverProfile | None:
        self._uow.begin()
        try:
            driver = self._uow.drivers.find_by_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return driver

    def _load(self, application_id: UUID) -> DriverApplication:
        application = self._uow.applications.get(application_id)
        if application is None:
            raise ApplicationNotFound(str(application_id))
        return application
