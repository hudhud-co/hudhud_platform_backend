"""The Workforce services against the database their own migration built.

The unit tests run on the in-memory unit of work, where saving an entity is putting
it in a dict. That hides everything about *how* a row reaches PostgreSQL — which is
where starting a shift for the first time turned out to fail: the store inferred
"this entity already has a row" from its version number, and a brand-new attendance
record is version-bumped to 2 before it is ever saved.

So these drive the real services through a real database.
"""

from __future__ import annotations

import pytest

from .helpers import (
    alembic,
    docker_available,
    run_in_service,
    start_postgres,
    stop_postgres,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not docker_available(), reason="Docker is required for the store proof"
    ),
]

SERVICE = "workforce"

_PRELUDE = """
import os
from datetime import UTC, date, datetime, time
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from workforce.application.attendance_service import AttendanceService, LatenessPolicy
from workforce.application.onboarding_service import ApplicationDraft, OnboardingService
from workforce.domain.value_objects import (
    AttendanceStatus,
    ShiftSlot,
    ShiftWindow,
    VehicleDetails,
    Weekday,
)
from workforce.infrastructure.persistence.sqlalchemy_store import (
    SqlAlchemyWorkforceUnitOfWork,
)

URL = os.environ["WORKFORCE_DATABASE_URL"]
engine = create_engine(URL)
uow = SqlAlchemyWorkforceUnitOfWork(session_factory=sessionmaker(bind=engine))

onboarding = OnboardingService(uow)
attendance = AttendanceService(uow, policy=LatenessPolicy())


def a_verified_driver(weekday, slot, starts, ends):
    \"\"\"Submit, verify at the office, and return the driver id.\"\"\"
    documents = (
        "ID_CARD",
        "DRIVING_LICENCE",
        "VEHICLE_REGISTRATION",
        "INSURANCE_CERTIFICATE",
    )
    application = onboarding.submit_application(
        applicant_principal_id=uuid4(),
        draft=ApplicationDraft(
            full_name="Kareem Hassan Al-Obaidi",
            vehicle=VehicleDetails(
                kind="VAN", plate_number="24A3391", model="Kia Bongo"
            ),
            declared_shifts=(
                ShiftWindow(
                    weekday=weekday, slot=slot, starts_at=starts, ends_at=ends
                ),
            ),
            terms_version="v6.3",
            documents_received=documents,
        ),
    )
    verified = onboarding.record_office_verification(
        application_id=application.application_id,
        operator_principal_id=uuid4(),
        office="Karbala office",
        documents_verified=documents,
        effective_from=date(2026, 9, 15),
    )
    return verified.driver.driver_id
"""


@pytest.fixture(scope="module")
def lab():
    started = start_postgres(f"{SERVICE}-store")
    try:
        upgrade = alembic(SERVICE, started, "upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr[-4000:]
        yield started
    finally:
        stop_postgres(started)


def run(lab, body: str) -> str:
    result = run_in_service(SERVICE, _PRELUDE + body, lab)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout


def test_a_driver_can_start_their_first_shift(lab) -> None:
    """The first shift of a driver's life, which has no attendance row yet."""
    run(
        lab,
        """
on_time = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
driver = a_verified_driver(Weekday.TUESDAY, ShiftSlot.MORNING, time(7, 0), time(11, 0))

result = attendance.start_shift(driver_id=driver, moment=on_time)

assert result.attendance.status is AttendanceStatus.STARTED, result.attendance.status
assert result.attendance.lateness_minutes == 0, result.attendance.lateness_minutes
assert result.block is None, result.block

uow.begin()
stored = uow.attendance.find(driver, date(2026, 9, 15))
uow.commit()
assert stored is not None, "the attendance row was never written"
assert stored.status is AttendanceStatus.STARTED, stored.status
""",
    )


def test_starting_late_opens_a_block_and_records_the_lateness(lab) -> None:
    run(
        lab,
        """
late = datetime(2026, 9, 15, 8, 30, tzinfo=UTC)
driver = a_verified_driver(Weekday.TUESDAY, ShiftSlot.MORNING, time(7, 0), time(11, 0))

result = attendance.start_shift(driver_id=driver, moment=late)

assert result.attendance.lateness_minutes == 90, result.attendance.lateness_minutes
assert result.block is not None, "a 90-minute late start must open a block"

uow.begin()
open_block = uow.blocks.find_active(driver)
uow.commit()
assert open_block is not None, "the block row was never written"
""",
    )


def test_a_second_start_on_the_same_day_is_refused_not_duplicated(lab) -> None:
    run(
        lab,
        """
from workforce.domain.errors import ShiftAlreadyStarted

on_time = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
driver = a_verified_driver(Weekday.TUESDAY, ShiftSlot.MORNING, time(7, 0), time(11, 0))
attendance.start_shift(driver_id=driver, moment=on_time)

try:
    attendance.start_shift(driver_id=driver, moment=on_time)
except ShiftAlreadyStarted:
    pass
else:
    raise AssertionError("a second start must be refused")

uow.begin()
rows = uow.attendance.list_for_driver(driver)
uow.commit()
assert len(rows) == 1, rows
""",
    )
