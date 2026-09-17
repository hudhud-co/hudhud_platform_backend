"""PostgreSQL unit of work for the Workforce service."""

from __future__ import annotations

from datetime import date, time
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from workforce.domain.entities import (
    AttendanceRecord,
    DriverApplication,
    DriverProfile,
    LatenessBlock,
    LeaveRequest,
    ShiftPattern,
)
from workforce.domain.errors import StaleWorkforceRecord
from workforce.domain.messaging import InboxRecord, InboxStatus
from workforce.domain.value_objects import (
    APPLICATION_TRANSITIONS,
    ApplicationStatus,
    AttendanceStatus,
    BlockReason,
    DriverStatus,
    LeaveKind,
    LeaveReason,
    LeaveStatus,
    LeaveWindow,
    ShiftSlot,
    ShiftWindow,
    VehicleDetails,
    Weekday,
)
from workforce.infrastructure.persistence.models import (
    AttendanceRow,
    DriverApplicationRow,
    DriverProfileRow,
    IntegrationInboxRow,
    LatenessBlockRow,
    LeaveRequestRow,
    ShiftPatternRow,
)

_OPEN_APPLICATION_VALUES = tuple(
    status.value for status, onward in APPLICATION_TRANSITIONS.items() if onward
)
_PROTECTING_LEAVE_VALUES = (LeaveStatus.PENDING.value, LeaveStatus.APPROVED.value)

_STAGES = (
    "applications",
    "drivers",
    "patterns",
    "attendance",
    "blocks",
    "leave",
    "inbox",
)


class SqlAlchemyWorkforceUnitOfWork:
    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._applications = _ApplicationRepo(self)
        self._drivers = _DriverRepo(self)
        self._patterns = _PatternRepo(self)
        self._attendance = _AttendanceRepo(self)
        self._blocks = _BlockRepo(self)
        self._leave = _LeaveRepo(self)
        self._inbox = _InboxRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entity, previous in pending["applications"].values():
            _versioned(
                session,
                DriverApplicationRow,
                DriverApplicationRow.application_id == entity.application_id,
                previous=previous,
                values=_application_values(entity),
                new_row=lambda e=entity: DriverApplicationRow(
                    application_id=e.application_id, **_application_values(e)
                ),
            )
        for entity, previous in pending["drivers"].values():
            _versioned(
                session,
                DriverProfileRow,
                DriverProfileRow.driver_id == entity.driver_id,
                previous=previous,
                values=_driver_values(entity),
                new_row=lambda e=entity: DriverProfileRow(
                    driver_id=e.driver_id, **_driver_values(e)
                ),
            )
        for entity, previous in pending["patterns"].values():
            _versioned(
                session,
                ShiftPatternRow,
                ShiftPatternRow.pattern_id == entity.pattern_id,
                previous=previous,
                values=_pattern_values(entity),
                new_row=lambda e=entity: ShiftPatternRow(
                    pattern_id=e.pattern_id, **_pattern_values(e)
                ),
            )
        for entity, previous in pending["attendance"].values():
            _versioned(
                session,
                AttendanceRow,
                AttendanceRow.attendance_id == entity.attendance_id,
                previous=previous,
                values=_attendance_values(entity),
                new_row=lambda e=entity: AttendanceRow(
                    attendance_id=e.attendance_id, **_attendance_values(e)
                ),
            )
        for entity, previous in pending["blocks"].values():
            _versioned(
                session,
                LatenessBlockRow,
                LatenessBlockRow.block_id == entity.block_id,
                previous=previous,
                values=_block_values(entity),
                new_row=lambda e=entity: LatenessBlockRow(
                    block_id=e.block_id, **_block_values(e)
                ),
            )
        for entity, previous in pending["leave"].values():
            _versioned(
                session,
                LeaveRequestRow,
                LeaveRequestRow.leave_id == entity.leave_id,
                previous=previous,
                values=_leave_values(entity),
                new_row=lambda e=entity: LeaveRequestRow(
                    leave_id=e.leave_id, **_leave_values(e)
                ),
            )
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

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


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount:
        return
    # `previous` is inferred from the entity's version by `_stage`, which cannot tell a
    # loaded-and-mutated entity from one the domain created and version-bumped inside
    # this same transaction. Starting a first shift does exactly that: the attendance
    # record is new, is bumped to version 2, and has no row to update.
    #
    # Ask the database which case this is rather than guessing. A genuine concurrent
    # change still fails here, because the row is present at a different version.
    if session.execute(select(row_cls.version).where(where)).first() is None:
        session.add(new_row())
        return
    raise StaleWorkforceRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyWorkforceUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _ApplicationRepo(_Repo):
    def save(self, application: DriverApplication) -> None:
        _stage(
            self._stage_for("applications"),
            application.application_id,
            application,
            application.version,
        )

    def get(self, application_id: UUID) -> DriverApplication | None:
        staged = self._stage_for("applications").get(application_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DriverApplicationRow, application_id)
        return _application_entity(row) if row is not None else None

    def find_open_for_principal(self, principal_id: UUID) -> DriverApplication | None:
        row = (
            self._session()
            .execute(
                select(DriverApplicationRow).where(
                    DriverApplicationRow.applicant_principal_id == principal_id,
                    DriverApplicationRow.status.in_(_OPEN_APPLICATION_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _application_entity(row) if row is not None else None

    def find_by_reference(self, reference: str) -> DriverApplication | None:
        row = (
            self._session()
            .execute(
                select(DriverApplicationRow).where(
                    DriverApplicationRow.reference == reference
                )
            )
            .scalars()
            .first()
        )
        return _application_entity(row) if row is not None else None


class _DriverRepo(_Repo):
    def save(self, driver: DriverProfile) -> None:
        _stage(self._stage_for("drivers"), driver.driver_id, driver, driver.version)

    def get(self, driver_id: UUID) -> DriverProfile | None:
        staged = self._stage_for("drivers").get(driver_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DriverProfileRow, driver_id)
        return _driver_entity(row) if row is not None else None

    def find_by_principal(self, principal_id: UUID) -> DriverProfile | None:
        for staged, _ in self._stage_for("drivers").values():
            if staged.principal_id == principal_id:
                return staged
        row = (
            self._session()
            .execute(
                select(DriverProfileRow).where(
                    DriverProfileRow.principal_id == principal_id
                )
            )
            .scalars()
            .first()
        )
        return _driver_entity(row) if row is not None else None


class _PatternRepo(_Repo):
    def save(self, pattern: ShiftPattern) -> None:
        _stage(self._stage_for("patterns"), pattern.pattern_id, pattern, pattern.version)

    def current_for(self, driver_id: UUID, day: date) -> ShiftPattern | None:
        rows = (
            self._session()
            .execute(
                select(ShiftPatternRow)
                .where(
                    ShiftPatternRow.driver_id == driver_id,
                    ShiftPatternRow.effective_from <= day,
                )
                .order_by(ShiftPatternRow.effective_from.desc())
            )
            .scalars()
            .all()
        )
        for row in rows:
            pattern = _pattern_entity(row)
            if pattern.applies_on(day):
                return pattern
        return None

    def list_for_driver(self, driver_id: UUID) -> tuple[ShiftPattern, ...]:
        rows = (
            self._session()
            .execute(
                select(ShiftPatternRow).where(ShiftPatternRow.driver_id == driver_id)
            )
            .scalars()
            .all()
        )
        return tuple(_pattern_entity(row) for row in rows)


class _AttendanceRepo(_Repo):
    def save(self, record: AttendanceRecord) -> None:
        _stage(
            self._stage_for("attendance"),
            record.attendance_id,
            record,
            record.version,
        )

    def find(self, driver_id: UUID, shift_date: date) -> AttendanceRecord | None:
        for staged, _ in self._stage_for("attendance").values():
            if staged.driver_id == driver_id and staged.shift_date == shift_date:
                return staged
        row = (
            self._session()
            .execute(
                select(AttendanceRow).where(
                    AttendanceRow.driver_id == driver_id,
                    AttendanceRow.shift_date == shift_date,
                )
            )
            .scalars()
            .first()
        )
        return _attendance_entity(row) if row is not None else None

    def list_for_driver(self, driver_id: UUID) -> tuple[AttendanceRecord, ...]:
        rows = (
            self._session()
            .execute(select(AttendanceRow).where(AttendanceRow.driver_id == driver_id))
            .scalars()
            .all()
        )
        return tuple(_attendance_entity(row) for row in rows)


class _BlockRepo(_Repo):
    def save(self, block: LatenessBlock) -> None:
        _stage(self._stage_for("blocks"), block.block_id, block, block.version)

    def get(self, block_id: UUID) -> LatenessBlock | None:
        staged = self._stage_for("blocks").get(block_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(LatenessBlockRow, block_id)
        return _block_entity(row) if row is not None else None

    def find_active(self, driver_id: UUID) -> LatenessBlock | None:
        for staged, _ in self._stage_for("blocks").values():
            if staged.driver_id == driver_id and staged.is_active:
                return staged
        row = (
            self._session()
            .execute(
                select(LatenessBlockRow).where(
                    LatenessBlockRow.driver_id == driver_id,
                    LatenessBlockRow.cleared_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        return _block_entity(row) if row is not None else None

    def list_active(self) -> tuple[LatenessBlock, ...]:
        rows = (
            self._session()
            .execute(
                select(LatenessBlockRow).where(LatenessBlockRow.cleared_at.is_(None))
            )
            .scalars()
            .all()
        )
        return tuple(_block_entity(row) for row in rows)


class _LeaveRepo(_Repo):
    def save(self, request: LeaveRequest) -> None:
        _stage(self._stage_for("leave"), request.leave_id, request, request.version)

    def get(self, leave_id: UUID) -> LeaveRequest | None:
        staged = self._stage_for("leave").get(leave_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(LeaveRequestRow, leave_id)
        return _leave_entity(row) if row is not None else None

    def list_for_driver(self, driver_id: UUID) -> tuple[LeaveRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(LeaveRequestRow).where(LeaveRequestRow.driver_id == driver_id)
            )
            .scalars()
            .all()
        )
        return tuple(_leave_entity(row) for row in rows)

    def list_pending(self) -> tuple[LeaveRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(LeaveRequestRow).where(
                    LeaveRequestRow.status == LeaveStatus.PENDING.value
                )
            )
            .scalars()
            .all()
        )
        return tuple(_leave_entity(row) for row in rows)

    def find_covering(self, driver_id: UUID, day: date) -> LeaveRequest | None:
        for staged, _ in self._stage_for("leave").values():
            if (
                staged.driver_id == driver_id
                and staged.window.covers_date(day)
                and staged.protects_from_lateness
            ):
                return staged
        rows = (
            self._session()
            .execute(
                select(LeaveRequestRow)
                .where(
                    LeaveRequestRow.driver_id == driver_id,
                    LeaveRequestRow.start_date <= day,
                    LeaveRequestRow.end_date >= day,
                    LeaveRequestRow.status.in_(_PROTECTING_LEAVE_VALUES),
                )
                # Approved beats pending: it is the settled answer.
                .order_by(LeaveRequestRow.status.desc())
            )
            .scalars()
            .all()
        )
        return _leave_entity(rows[0]) if rows else None


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (record, True)

    def save(self, record: InboxRecord) -> None:
        stage = self._stage_for("inbox")
        key = (record.consumer_name, record.event_id)
        is_new = stage[key][1] if key in stage else False
        stage[key] = (record, is_new)


# ------------------------------------------------------------------ mappers


def _window_json(window: ShiftWindow) -> dict[str, object]:
    return {
        "weekday": int(window.weekday),
        "slot": window.slot.value,
        "starts_at": window.starts_at.isoformat(timespec="minutes"),
        "ends_at": window.ends_at.isoformat(timespec="minutes"),
    }


def _window_entity(raw: dict) -> ShiftWindow:
    return ShiftWindow(
        weekday=Weekday(int(raw["weekday"])),
        slot=ShiftSlot(raw["slot"]),
        starts_at=time.fromisoformat(raw["starts_at"]),
        ends_at=time.fromisoformat(raw["ends_at"]),
    )


def _application_values(e: DriverApplication) -> dict[str, object]:
    return {
        "applicant_principal_id": e.applicant_principal_id,
        "reference": e.reference,
        "status": e.status.value,
        "full_name": e.full_name,
        "vehicle_kind": e.vehicle.kind,
        "vehicle_plate_number": e.vehicle.plate_number,
        "vehicle_model": e.vehicle.model,
        "declared_shifts": [_window_json(window) for window in e.declared_shifts],
        "terms_version_accepted": e.terms_version_accepted,
        "documents_received": list(e.documents_received),
        "documents_verified": list(e.documents_verified),
        "submitted_at": e.submitted_at,
        "verified_at": e.verified_at,
        "verified_by_actor_id": e.verified_by_actor_id,
        "verified_at_office": e.verified_at_office,
        "decision_reason": e.decision_reason,
        "version": e.version,
    }


def _application_entity(row: DriverApplicationRow) -> DriverApplication:
    return DriverApplication(
        application_id=row.application_id,  # type: ignore[arg-type]
        applicant_principal_id=row.applicant_principal_id,  # type: ignore[arg-type]
        reference=row.reference,
        status=ApplicationStatus(row.status),
        full_name=row.full_name,
        vehicle=VehicleDetails(
            kind=row.vehicle_kind,
            plate_number=row.vehicle_plate_number,
            model=row.vehicle_model,
        ),
        declared_shifts=tuple(
            _window_entity(raw) for raw in (row.declared_shifts or [])
        ),
        terms_version_accepted=row.terms_version_accepted,
        documents_received=tuple(row.documents_received or ()),
        documents_verified=tuple(row.documents_verified or ()),
        submitted_at=row.submitted_at,  # type: ignore[arg-type]
        verified_at=row.verified_at,  # type: ignore[arg-type]
        verified_by_actor_id=row.verified_by_actor_id,  # type: ignore[arg-type]
        verified_at_office=row.verified_at_office,
        decision_reason=row.decision_reason,
        version=row.version,
    )


def _driver_values(e: DriverProfile) -> dict[str, object]:
    return {
        "principal_id": e.principal_id,
        "application_id": e.application_id,
        "full_name": e.full_name,
        "vehicle_kind": e.vehicle.kind,
        "vehicle_plate_number": e.vehicle.plate_number,
        "vehicle_model": e.vehicle.model,
        "status": e.status.value,
        "activated_at": e.activated_at,
        "version": e.version,
    }


def _driver_entity(row: DriverProfileRow) -> DriverProfile:
    return DriverProfile(
        driver_id=row.driver_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        application_id=row.application_id,  # type: ignore[arg-type]
        full_name=row.full_name,
        vehicle=VehicleDetails(
            kind=row.vehicle_kind,
            plate_number=row.vehicle_plate_number,
            model=row.vehicle_model,
        ),
        status=DriverStatus(row.status),
        activated_at=row.activated_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _pattern_values(e: ShiftPattern) -> dict[str, object]:
    return {
        "driver_id": e.driver_id,
        "windows": [_window_json(window) for window in e.windows],
        "effective_from": e.effective_from,
        "effective_to": e.effective_to,
        "version": e.version,
    }


def _pattern_entity(row: ShiftPatternRow) -> ShiftPattern:
    return ShiftPattern(
        pattern_id=row.pattern_id,  # type: ignore[arg-type]
        driver_id=row.driver_id,  # type: ignore[arg-type]
        windows=tuple(_window_entity(raw) for raw in (row.windows or [])),
        effective_from=row.effective_from,  # type: ignore[arg-type]
        effective_to=row.effective_to,  # type: ignore[arg-type]
        version=row.version,
    )


def _attendance_values(e: AttendanceRecord) -> dict[str, object]:
    return {
        "driver_id": e.driver_id,
        "shift_date": e.shift_date,
        "scheduled_start": e.scheduled_start,
        "status": e.status.value,
        "started_at": e.started_at,
        "lateness_minutes": e.lateness_minutes,
        "version": e.version,
    }


def _attendance_entity(row: AttendanceRow) -> AttendanceRecord:
    return AttendanceRecord(
        attendance_id=row.attendance_id,  # type: ignore[arg-type]
        driver_id=row.driver_id,  # type: ignore[arg-type]
        shift_date=row.shift_date,  # type: ignore[arg-type]
        scheduled_start=row.scheduled_start,  # type: ignore[arg-type]
        status=AttendanceStatus(row.status),
        started_at=row.started_at,  # type: ignore[arg-type]
        lateness_minutes=row.lateness_minutes,
        version=row.version,
    )


def _block_values(e: LatenessBlock) -> dict[str, object]:
    return {
        "driver_id": e.driver_id,
        "reason": e.reason.value,
        "shift_date": e.shift_date,
        "scheduled_start": e.scheduled_start,
        "opened_app_at": e.opened_app_at,
        "delay_minutes": e.delay_minutes,
        "blocked_since": e.blocked_since,
        "unblock_requested_at": e.unblock_requested_at,
        "cleared_at": e.cleared_at,
        "cleared_by_actor_id": e.cleared_by_actor_id,
        "clearing_note": e.clearing_note,
        "version": e.version,
    }


def _block_entity(row: LatenessBlockRow) -> LatenessBlock:
    return LatenessBlock(
        block_id=row.block_id,  # type: ignore[arg-type]
        driver_id=row.driver_id,  # type: ignore[arg-type]
        reason=BlockReason(row.reason),
        shift_date=row.shift_date,  # type: ignore[arg-type]
        scheduled_start=row.scheduled_start,  # type: ignore[arg-type]
        opened_app_at=row.opened_app_at,  # type: ignore[arg-type]
        delay_minutes=row.delay_minutes,
        blocked_since=row.blocked_since,  # type: ignore[arg-type]
        unblock_requested_at=row.unblock_requested_at,  # type: ignore[arg-type]
        cleared_at=row.cleared_at,  # type: ignore[arg-type]
        cleared_by_actor_id=row.cleared_by_actor_id,  # type: ignore[arg-type]
        clearing_note=row.clearing_note,
        version=row.version,
    )


def _leave_values(e: LeaveRequest) -> dict[str, object]:
    return {
        "driver_id": e.driver_id,
        "reference": e.reference,
        "reason": e.reason.value,
        "kind": e.window.kind.value,
        "start_date": e.window.start_date,
        "end_date": e.window.end_date,
        "starts_at": e.window.starts_at,
        "ends_at": e.window.ends_at,
        "status": e.status.value,
        "note": e.note,
        "requested_at": e.requested_at,
        "decided_at": e.decided_at,
        "decided_by_actor_id": e.decided_by_actor_id,
        "decision_note": e.decision_note,
        "version": e.version,
    }


def _leave_entity(row: LeaveRequestRow) -> LeaveRequest:
    return LeaveRequest(
        leave_id=row.leave_id,  # type: ignore[arg-type]
        driver_id=row.driver_id,  # type: ignore[arg-type]
        reference=row.reference,
        reason=LeaveReason(row.reason),
        window=LeaveWindow(
            kind=LeaveKind(row.kind),
            start_date=row.start_date,  # type: ignore[arg-type]
            end_date=row.end_date,  # type: ignore[arg-type]
            starts_at=row.starts_at,  # type: ignore[arg-type]
            ends_at=row.ends_at,  # type: ignore[arg-type]
        ),
        status=LeaveStatus(row.status),
        note=row.note,
        requested_at=row.requested_at,  # type: ignore[arg-type]
        decided_at=row.decided_at,  # type: ignore[arg-type]
        decided_by_actor_id=row.decided_by_actor_id,  # type: ignore[arg-type]
        decision_note=row.decision_note,
        version=row.version,
    )


def _inbox_values(e: InboxRecord) -> dict[str, object]:
    return {
        "inbox_id": e.inbox_id,
        "consumer_name": e.consumer_name,
        "event_id": e.event_id,
        **_inbox_update_values(e),
    }


def _inbox_update_values(e: InboxRecord) -> dict[str, object]:
    return {
        "event_type": e.event_type,
        "event_version": e.event_version,
        "status": e.status.value,
        "received_at": e.received_at,
        "attempt_count": e.attempt_count,
        "processing_started_at": e.processing_started_at,
        "processing_lease_until": e.processing_lease_until,
        "processed_at": e.processed_at,
        "last_error_code": e.last_error_code,
        "payload_json": e.payload_json,
    }


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,  # type: ignore[arg-type]
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        payload_json=dict(row.payload_json or {}),
    )
