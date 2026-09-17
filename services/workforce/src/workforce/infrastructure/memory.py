"""In-memory Workforce unit of work for unit tests."""

from __future__ import annotations

import copy
from datetime import date
from uuid import UUID

from workforce.domain.entities import (
    AttendanceRecord,
    DriverApplication,
    DriverProfile,
    LatenessBlock,
    LeaveRequest,
    ShiftPattern,
)
from workforce.domain.messaging import InboxRecord
from workforce.domain.value_objects import (
    APPLICATION_TRANSITIONS,
    LeaveStatus,
)

_COLLECTIONS = (
    "applications",
    "drivers",
    "patterns",
    "attendance",
    "blocks",
    "leave",
    "inbox",
)


class InMemoryWorkforceDatabase:
    """The rows. Shared by every request, exactly as a database is.

    Split from the unit of work deliberately. A unit of work is *one request's*
    transaction; the rows outlive it. Holding both in one object is what allowed a single
    instance to be stored on `app.state` and serve every request at once — measured
    against real PostgreSQL, 1 of 40 concurrent requests succeeded and the rest raised
    "transaction already active".
    """

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {name: {} for name in _COLLECTIONS}


class InMemoryWorkforceUnitOfWork:
    def __init__(self, database: InMemoryWorkforceDatabase | None = None) -> None:
        self._database = database or InMemoryWorkforceDatabase()
        self._tx: dict[str, dict] | None = None
        self._applications = _ApplicationRepo(self)
        self._drivers = _DriverRepo(self)
        self._patterns = _PatternRepo(self)
        self._attendance = _AttendanceRepo(self)
        self._blocks = _BlockRepo(self)
        self._leave = _LeaveRepo(self)
        self._inbox = _InboxRepo(self)

    @property
    def database(self) -> InMemoryWorkforceDatabase:
        """The shared rows, so a factory can give the next request the same data."""
        return self._database

    def new_unit_of_work(self) -> InMemoryWorkforceUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryWorkforceUnitOfWork(self._database)


    def as_committed(self) -> InMemoryWorkforceUnitOfWork:
        """A view whose "transaction" is the committed rows, for inspection and seeding.

        Service code never uses this: a service opens a real transaction, and
        :meth:`_working` refuses it otherwise. Tests use it to look at what was committed
        without opening one, and to seed rows before exercising a service — both of which
        would otherwise have to wrap every assertion in begin/rollback and would read far
        worse for no extra safety.

        Writes through this view land straight in the committed rows, which is what
        seeding wants and why it is named for it.
        """
        view = InMemoryWorkforceUnitOfWork(self._database)
        view._tx = self._database.collections
        return view

    def begin(self) -> None:
        if self._tx is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx = copy.deepcopy(self._database.collections)

    def commit(self) -> None:
        if self._tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._database.collections = self._tx
        self._tx = None

    def rollback(self) -> None:
        self._tx = None

    def _working(self, name: str) -> dict:
        """Refuse a read or a write outside a transaction, as the store does.

        This used to fall back to the committed rows, making the double *more*
        permissive than SQLAlchemy — so a service method that forgot to open a
        transaction passed every unit test and failed only against PostgreSQL.
        """
        if self._tx is None:
            msg = (
                f"no active transaction: {name} was accessed outside begin()/commit(). "
                "The SQLAlchemy store raises here too."
            )
            raise RuntimeError(msg)
        return self._tx[name]

    @property
    def applications(self) -> _ApplicationRepo:
        return self._applications

    @property
    def drivers(self) -> _DriverRepo:
        return self._drivers

    @property
    def patterns(self) -> _PatternRepo:
        return self._patterns

    @property
    def attendance(self) -> _AttendanceRepo:
        return self._attendance

    @property
    def blocks(self) -> _BlockRepo:
        return self._blocks

    @property
    def leave(self) -> _LeaveRepo:
        return self._leave

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


class _Repo:
    def __init__(self, store: InMemoryWorkforceUnitOfWork) -> None:
        self._store = store


class _ApplicationRepo(_Repo):
    def save(self, application: DriverApplication) -> None:
        self._store._working("applications")[application.application_id] = copy.deepcopy(
            application
        )

    def get(self, application_id: UUID) -> DriverApplication | None:
        found = self._store._working("applications").get(application_id)
        return copy.deepcopy(found) if found is not None else None

    def find_open_for_principal(self, principal_id: UUID) -> DriverApplication | None:
        for application in self._store._working("applications").values():
            if application.applicant_principal_id == principal_id and (
                APPLICATION_TRANSITIONS[application.status]
            ):
                return copy.deepcopy(application)
        return None

    def find_by_reference(self, reference: str) -> DriverApplication | None:
        for application in self._store._working("applications").values():
            if application.reference == reference:
                return copy.deepcopy(application)
        return None


class _DriverRepo(_Repo):
    def save(self, driver: DriverProfile) -> None:
        self._store._working("drivers")[driver.driver_id] = copy.deepcopy(driver)

    def get(self, driver_id: UUID) -> DriverProfile | None:
        found = self._store._working("drivers").get(driver_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_principal(self, principal_id: UUID) -> DriverProfile | None:
        for driver in self._store._working("drivers").values():
            if driver.principal_id == principal_id:
                return copy.deepcopy(driver)
        return None


class _PatternRepo(_Repo):
    def save(self, pattern: ShiftPattern) -> None:
        self._store._working("patterns")[pattern.pattern_id] = copy.deepcopy(pattern)

    def current_for(self, driver_id: UUID, day: date) -> ShiftPattern | None:
        candidates = [
            pattern
            for pattern in self._store._working("patterns").values()
            if pattern.driver_id == driver_id and pattern.applies_on(day)
        ]
        if not candidates:
            return None
        # The latest pattern whose window covers the day wins.
        return copy.deepcopy(max(candidates, key=lambda item: item.effective_from))

    def list_for_driver(self, driver_id: UUID) -> tuple[ShiftPattern, ...]:
        return tuple(
            copy.deepcopy(pattern)
            for pattern in self._store._working("patterns").values()
            if pattern.driver_id == driver_id
        )


class _AttendanceRepo(_Repo):
    def save(self, record: AttendanceRecord) -> None:
        self._store._working("attendance")[record.attendance_id] = copy.deepcopy(record)

    def find(self, driver_id: UUID, shift_date: date) -> AttendanceRecord | None:
        for record in self._store._working("attendance").values():
            if record.driver_id == driver_id and record.shift_date == shift_date:
                return copy.deepcopy(record)
        return None

    def list_for_driver(self, driver_id: UUID) -> tuple[AttendanceRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("attendance").values()
            if record.driver_id == driver_id
        )


class _BlockRepo(_Repo):
    def save(self, block: LatenessBlock) -> None:
        self._store._working("blocks")[block.block_id] = copy.deepcopy(block)

    def get(self, block_id: UUID) -> LatenessBlock | None:
        found = self._store._working("blocks").get(block_id)
        return copy.deepcopy(found) if found is not None else None

    def find_active(self, driver_id: UUID) -> LatenessBlock | None:
        for block in self._store._working("blocks").values():
            if block.driver_id == driver_id and block.is_active:
                return copy.deepcopy(block)
        return None

    def list_active(self) -> tuple[LatenessBlock, ...]:
        return tuple(
            copy.deepcopy(block)
            for block in self._store._working("blocks").values()
            if block.is_active
        )


class _LeaveRepo(_Repo):
    def save(self, request: LeaveRequest) -> None:
        self._store._working("leave")[request.leave_id] = copy.deepcopy(request)

    def get(self, leave_id: UUID) -> LeaveRequest | None:
        found = self._store._working("leave").get(leave_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_driver(self, driver_id: UUID) -> tuple[LeaveRequest, ...]:
        return tuple(
            copy.deepcopy(request)
            for request in self._store._working("leave").values()
            if request.driver_id == driver_id
        )

    def list_pending(self) -> tuple[LeaveRequest, ...]:
        return tuple(
            copy.deepcopy(request)
            for request in self._store._working("leave").values()
            if request.status is LeaveStatus.PENDING
        )

    def find_covering(self, driver_id: UUID, day: date) -> LeaveRequest | None:
        live = [
            request
            for request in self._store._working("leave").values()
            if request.driver_id == driver_id
            and request.window.covers_date(day)
            and request.protects_from_lateness
        ]
        if not live:
            return None
        # Approved beats pending when both cover the day: it is the settled answer.
        approved = [r for r in live if r.status is LeaveStatus.APPROVED]
        return copy.deepcopy(approved[0] if approved else live[0])


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        found = self._store._working("inbox").get((consumer_name, event_id))
        return copy.deepcopy(found) if found is not None else None

    def insert(self, record: InboxRecord) -> None:
        rows = self._store._working("inbox")
        key = (record.consumer_name, record.event_id)
        if key in rows:
            msg = f"duplicate inbox delivery: {key}"
            raise ValueError(msg)
        rows[key] = copy.deepcopy(record)

    def save(self, record: InboxRecord) -> None:
        self._store._working("inbox")[
            (record.consumer_name, record.event_id)
        ] = copy.deepcopy(record)
