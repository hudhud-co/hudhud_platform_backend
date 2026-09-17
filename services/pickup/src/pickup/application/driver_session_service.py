"""Driver pickup-capability work session lifecycle.

Availability is *derived* from the session, never asserted by the client, and the
transitions that release a driver from the field are gated on Pickup-owned operational
blockers (open custody, outstanding hub handover, unreconciled offline work).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from pickup.application.history import record_history
from pickup.application.workload import DriverWorkload, collect_driver_workload
from pickup.domain.errors import (
    DriverWorkSessionAlreadyOpen,
    DriverWorkSessionBlocked,
    DriverWorkSessionNotFound,
    DriverWorkSessionNotOpen,
    InvalidWorkSessionReason,
)
from pickup.domain.workforce import (
    DriverAvailabilityStatus,
    DriverCapability,
    DriverWorkSession,
    WorkSessionEndReason,
    WorkSessionPauseReason,
    WorkSessionStatus,
    derive_availability,
)
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import DriverWorkUnitOfWork

MAX_NOTE_LENGTH = 512


@dataclass(frozen=True, slots=True)
class StartWorkSessionCommand:
    driver_user_id: str
    occurred_at: datetime
    home_hub_id: UUID | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PauseWorkSessionCommand:
    driver_user_id: str
    session_id: UUID
    reason: WorkSessionPauseReason | str
    occurred_at: datetime
    notes: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeWorkSessionCommand:
    driver_user_id: str
    session_id: UUID
    occurred_at: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EndWorkSessionCommand:
    driver_user_id: str
    session_id: UUID
    reason: WorkSessionEndReason | str
    occurred_at: datetime
    notes: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkSessionResult:
    session: DriverWorkSession
    workload: DriverWorkload
    replayed: bool = False


class DriverWorkSessionService:
    """Start, pause, resume, and end one driver's pickup availability window."""

    def __init__(self, unit_of_work: DriverWorkUnitOfWork) -> None:
        self._uow = unit_of_work

    def start(self, command: StartWorkSessionCommand, *, actor: PickupActor) -> WorkSessionResult:
        self._uow.begin()
        try:
            existing = self._uow.work_sessions.get_open_session_for_driver(
                command.driver_user_id
            )
            if existing is not None:
                if existing.status is WorkSessionStatus.ACTIVE:
                    # Idempotent restart: the driver is already online for pickup.
                    result = WorkSessionResult(
                        session=existing,
                        workload=self._workload(command.driver_user_id),
                        replayed=True,
                    )
                    self._uow.commit()
                    return result
                raise DriverWorkSessionAlreadyOpen(
                    driver_user_id=command.driver_user_id,
                    session_id=str(existing.session_id),
                    status=existing.status.value,
                )

            session = DriverWorkSession(
                session_id=uuid4(),
                driver_user_id=command.driver_user_id,
                capability=DriverCapability.PICKUP,
                status=WorkSessionStatus.ACTIVE,
                availability=DriverAvailabilityStatus.ONLINE,
                started_at=command.occurred_at,
                home_hub_id=command.home_hub_id,
                version=1,
            )
            self._uow.work_sessions.save_session(session)
            self._record(
                action="work_session_started",
                actor=actor,
                session=session,
                previous_status=None,
                request_id=command.request_id,
                occurred_at=command.occurred_at,
            )
            result = WorkSessionResult(
                session=session,
                workload=self._workload(command.driver_user_id),
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def pause(self, command: PauseWorkSessionCommand, *, actor: PickupActor) -> WorkSessionResult:
        reason = _coerce_pause_reason(command.reason)
        _require_notes_for_other(reason.value, command.notes)
        self._uow.begin()
        try:
            session = self._load_owned(command.session_id, command.driver_user_id)
            if session.availability is DriverAvailabilityStatus.ON_BREAK:
                result = WorkSessionResult(
                    session=session,
                    workload=self._workload(command.driver_user_id),
                    replayed=True,
                )
                self._uow.commit()
                return result
            if session.status is not WorkSessionStatus.ACTIVE:
                raise DriverWorkSessionNotOpen(
                    session_id=str(session.session_id),
                    status=session.status.value,
                    required="ACTIVE",
                )
            workload = self._workload(command.driver_user_id)
            blockers = workload.pause_blockers()
            if blockers:
                raise DriverWorkSessionBlocked(
                    session_id=str(session.session_id),
                    blockers=blockers,
                )
            previous = session.status.value
            session.status = WorkSessionStatus.PAUSED
            session.availability = derive_availability(WorkSessionStatus.PAUSED)
            session.paused_at = command.occurred_at
            session.pause_reason = reason
            session.notes = _normalize_notes(command.notes)
            session.version += 1
            self._uow.work_sessions.save_session(session)
            self._record(
                action="work_session_paused",
                actor=actor,
                session=session,
                previous_status=previous,
                request_id=command.request_id,
                occurred_at=command.occurred_at,
                details={"reason": reason.value},
            )
            result = WorkSessionResult(session=session, workload=workload)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def resume(
        self, command: ResumeWorkSessionCommand, *, actor: PickupActor
    ) -> WorkSessionResult:
        self._uow.begin()
        try:
            session = self._load_owned(command.session_id, command.driver_user_id)
            if session.availability is DriverAvailabilityStatus.ONLINE:
                result = WorkSessionResult(
                    session=session,
                    workload=self._workload(command.driver_user_id),
                    replayed=True,
                )
                self._uow.commit()
                return result
            if session.status is not WorkSessionStatus.PAUSED:
                raise DriverWorkSessionNotOpen(
                    session_id=str(session.session_id),
                    status=session.status.value,
                    required="PAUSED",
                )
            previous = session.status.value
            session.status = WorkSessionStatus.ACTIVE
            session.availability = derive_availability(WorkSessionStatus.ACTIVE)
            session.resumed_at = command.occurred_at
            session.pause_reason = None
            session.version += 1
            self._uow.work_sessions.save_session(session)
            self._record(
                action="work_session_resumed",
                actor=actor,
                session=session,
                previous_status=previous,
                request_id=command.request_id,
                occurred_at=command.occurred_at,
            )
            result = WorkSessionResult(
                session=session,
                workload=self._workload(command.driver_user_id),
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def end(self, command: EndWorkSessionCommand, *, actor: PickupActor) -> WorkSessionResult:
        reason = _coerce_end_reason(command.reason)
        _require_notes_for_other(reason.value, command.notes)
        self._uow.begin()
        try:
            session = self._load_owned(command.session_id, command.driver_user_id)
            if session.status is WorkSessionStatus.ENDED:
                result = WorkSessionResult(
                    session=session,
                    workload=self._workload(command.driver_user_id),
                    replayed=True,
                )
                self._uow.commit()
                return result
            workload = self._workload(command.driver_user_id)
            blockers = workload.end_blockers()
            if blockers:
                raise DriverWorkSessionBlocked(
                    session_id=str(session.session_id),
                    blockers=blockers,
                )
            previous = session.status.value
            session.status = WorkSessionStatus.ENDED
            session.availability = derive_availability(WorkSessionStatus.ENDED)
            session.ended_at = command.occurred_at
            session.end_reason = reason
            session.notes = _normalize_notes(command.notes)
            session.version += 1
            self._uow.work_sessions.save_session(session)
            self._record(
                action="work_session_ended",
                actor=actor,
                session=session,
                previous_status=previous,
                request_id=command.request_id,
                occurred_at=command.occurred_at,
                details={"reason": reason.value},
            )
            result = WorkSessionResult(session=session, workload=workload)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def get_status(self, driver_user_id: str) -> WorkSessionResult:
        """Read-only availability projection — no transaction side effects."""
        session = self._uow.work_sessions.get_open_session_for_driver(driver_user_id)
        workload = self._workload(driver_user_id)
        if session is None:
            session = DriverWorkSession(
                session_id=UUID(int=0),
                driver_user_id=driver_user_id,
                capability=DriverCapability.PICKUP,
                status=WorkSessionStatus.ENDED,
                availability=DriverAvailabilityStatus.OFFLINE,
                started_at=datetime.min.replace(tzinfo=None),
                version=0,
            )
        return WorkSessionResult(session=session, workload=workload)

    def _load_owned(self, session_id: UUID, driver_user_id: str) -> DriverWorkSession:
        session = self._uow.work_sessions.get_session(session_id)
        if session is None or session.driver_user_id != driver_user_id:
            # Never distinguish "not yours" from "not found" to a caller.
            raise DriverWorkSessionNotFound(driver_user_id=driver_user_id)
        return session

    def _workload(self, driver_user_id: str) -> DriverWorkload:
        return collect_driver_workload(
            driver_user_id=driver_user_id,
            pickup_tasks=self._uow.pickup_tasks,
            handover_manifests=self._uow.handover_manifests,
            offline_events=self._uow.offline_events,
        )

    def _record(
        self,
        *,
        action: str,
        actor: PickupActor,
        session: DriverWorkSession,
        previous_status: str | None,
        request_id: str | None,
        occurred_at: datetime,
        details: dict[str, str] | None = None,
    ) -> None:
        record_history(
            self._uow.task_history,
            pickup_task_id=session.session_id,
            action=action,
            actor=actor,
            previous_status=previous_status,
            new_status=session.status.value,
            occurred_at=occurred_at,
            request_id=request_id,
            details={
                "capability": session.capability.value,
                "availability": session.availability.value,
                "driver_user_id": session.driver_user_id,
                **(details or {}),
            },
        )


def _coerce_pause_reason(value: WorkSessionPauseReason | str) -> WorkSessionPauseReason:
    if isinstance(value, WorkSessionPauseReason):
        return value
    try:
        return WorkSessionPauseReason(str(value).strip().upper())
    except ValueError as exc:
        raise InvalidWorkSessionReason(f"unknown pause reason: {value}") from exc


def _coerce_end_reason(value: WorkSessionEndReason | str) -> WorkSessionEndReason:
    if isinstance(value, WorkSessionEndReason):
        return value
    try:
        return WorkSessionEndReason(str(value).strip().upper())
    except ValueError as exc:
        raise InvalidWorkSessionReason(f"unknown end reason: {value}") from exc


def _require_notes_for_other(reason: str, notes: str | None) -> None:
    if reason == "OTHER" and not (notes or "").strip():
        raise InvalidWorkSessionReason("notes are required when reason is OTHER")


def _normalize_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    trimmed = notes.strip()
    if not trimmed:
        return None
    return trimmed[:MAX_NOTE_LENGTH]
