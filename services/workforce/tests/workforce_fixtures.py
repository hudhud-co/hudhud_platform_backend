"""Shared builders for Workforce tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

from workforce.application.attendance_service import AttendanceService, LatenessPolicy
from workforce.application.eligibility_service import EligibilityService
from workforce.application.inbox_service import InboxService
from workforce.application.leave_service import LeaveService
from workforce.application.onboarding_service import (
    ApplicationDraft,
    OnboardingService,
)
from workforce.domain.entities import DriverProfile
from workforce.domain.value_objects import (
    REQUIRED_OFFICE_DOCUMENTS,
    LeaveKind,
    LeaveWindow,
    ShiftSlot,
    ShiftWindow,
    VehicleDetails,
    Weekday,
)
from workforce.infrastructure.memory import InMemoryWorkforceUnitOfWork
from workforce.ports.authorization import WorkforceActor, WorkforceRole

APPLICANT = UUID("11111111-1111-4111-8111-111111111111")
OPERATOR = UUID("22222222-2222-4222-8222-222222222222")
SUPPORT = UUID("33333333-3333-4333-8333-333333333333")
OTHER_DRIVER = UUID("44444444-4444-4444-8444-444444444444")

#: 2026-09-14 is a Monday, so the fixture pattern and the fixture dates agree.
MONDAY = date(2026, 9, 14)
TUESDAY = date(2026, 9, 15)


def build_store() -> InMemoryWorkforceUnitOfWork:
    return InMemoryWorkforceUnitOfWork()


def onboarding_service(uow: InMemoryWorkforceUnitOfWork) -> OnboardingService:
    return OnboardingService(uow)


def attendance_service(
    uow: InMemoryWorkforceUnitOfWork, *, policy: LatenessPolicy | None = None
) -> AttendanceService:
    return AttendanceService(uow, policy=policy)


def leave_service(uow: InMemoryWorkforceUnitOfWork) -> LeaveService:
    return LeaveService(uow)


def eligibility_service(
    uow: InMemoryWorkforceUnitOfWork, **kwargs
) -> EligibilityService:
    return EligibilityService(uow, **kwargs)


def inbox_service(uow: InMemoryWorkforceUnitOfWork, **kwargs) -> InboxService:
    return InboxService(uow, **kwargs)


def a_vehicle(**kwargs) -> VehicleDetails:
    return VehicleDetails(
        kind=kwargs.pop("kind", "MOTORCYCLE"),
        plate_number=kwargs.pop("plate_number", "BG 12345"),
        **kwargs,
    )


def morning_shift(weekday: Weekday = Weekday.MONDAY) -> ShiftWindow:
    return ShiftWindow(
        weekday=weekday,
        slot=ShiftSlot.MORNING,
        starts_at=time(9, 0),
        ends_at=time(13, 0),
    )


def every_weekday_morning() -> tuple[ShiftWindow, ...]:
    return tuple(
        morning_shift(Weekday(day)) for day in range(1, 6)
    )


def a_draft(**kwargs) -> ApplicationDraft:
    return ApplicationDraft(
        full_name=kwargs.pop("full_name", "Hussein Jabbar"),
        vehicle=kwargs.pop("vehicle", a_vehicle()),
        declared_shifts=kwargs.pop("declared_shifts", every_weekday_morning()),
        terms_version=kwargs.pop("terms_version", "driver-1.0"),
        documents_received=kwargs.pop("documents_received", REQUIRED_OFFICE_DOCUMENTS),
    )


def verified_driver(
    uow: InMemoryWorkforceUnitOfWork,
    *,
    applicant: UUID = APPLICANT,
    effective_from: date = MONDAY,
    **draft_kwargs,
) -> DriverProfile:
    """Walk the real onboarding path — never hand-build a verified driver."""
    service = onboarding_service(uow)
    application = service.submit_application(
        applicant_principal_id=applicant, draft=a_draft(**draft_kwargs)
    )
    return service.record_office_verification(
        application_id=application.application_id,
        operator_principal_id=OPERATOR,
        office="Karbala office",
        documents_verified=REQUIRED_OFFICE_DOCUMENTS,
        effective_from=effective_from,
    ).driver


def at(hour: int = 9, minute: int = 0, day: date = MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def full_day_leave(start: date = MONDAY, end: date | None = None) -> LeaveWindow:
    return LeaveWindow(
        kind=LeaveKind.FULL_DAY, start_date=start, end_date=end or start
    )


def hourly_leave(day: date = MONDAY) -> LeaveWindow:
    return LeaveWindow(
        kind=LeaveKind.HOURLY,
        start_date=day,
        end_date=day,
        starts_at=time(9, 0),
        ends_at=time(13, 0),
    )


def driver_actor(principal_id: UUID = APPLICANT) -> WorkforceActor:
    return WorkforceActor(
        principal_id=principal_id, roles=frozenset({WorkforceRole.PICKUP_DRIVER})
    )


def support_actor() -> WorkforceActor:
    return WorkforceActor(
        principal_id=SUPPORT, roles=frozenset({WorkforceRole.SUPPORT})
    )


def operations_actor() -> WorkforceActor:
    return WorkforceActor(
        principal_id=OPERATOR, roles=frozenset({WorkforceRole.OPERATIONS})
    )


def new_id() -> UUID:
    return uuid4()
