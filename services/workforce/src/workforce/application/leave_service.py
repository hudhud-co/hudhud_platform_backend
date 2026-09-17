"""Leave requests and their decisions (DRV-A03, OPS-09).

Driver App v8 ``leave``: "Tell support why you can't work. Your ticket is reviewed right
away, and no lateness penalty applies while it is pending."

That last clause is the part with teeth. A request under review protects the driver before
anyone has decided anything, which is why `LeaveRequest.protects_from_lateness` covers
`PENDING` as well as `APPROVED`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from workforce.domain.entities import LeaveRequest
from workforce.domain.errors import (
    DriverNotFound,
    LeaveDecisionNoteRequired,
    LeaveRequestNotFound,
    LeaveTransitionNotAllowed,
    OnlySupportMayDecideLeave,
    OverlappingLeaveRequest,
)
from workforce.domain.value_objects import (
    LeaveReason,
    LeaveStatus,
    LeaveWindow,
)
from workforce.ports.repository import WorkforceUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


class LeaveService:
    def __init__(self, unit_of_work: WorkforceUnitOfWork) -> None:
        self._uow = unit_of_work

    def request_leave(
        self,
        *,
        driver_id: UUID,
        reason: LeaveReason,
        window: LeaveWindow,
        note: str | None = None,
    ) -> LeaveRequest:
        self._uow.begin()
        try:
            if self._uow.drivers.get(driver_id) is None:
                raise DriverNotFound(str(driver_id))
            clash = self._uow.leave.find_covering(driver_id, window.start_date)
            if clash is not None and clash.is_pending:
                raise OverlappingLeaveRequest(clash.reference)

            moment = _now()
            request = LeaveRequest(
                leave_id=uuid4(),
                driver_id=driver_id,
                reference=f"LV-{moment:%Y}-{secrets.randbelow(10_000):04d}",
                reason=reason,
                window=window,
                status=LeaveStatus.PENDING,
                note=(note or "").strip() or None,
                requested_at=moment,
            )
            self._uow.leave.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    def approve(
        self, *, leave_id: UUID, decider_principal_id: UUID, actor_may_decide: bool
    ) -> LeaveRequest:
        """OPS-09 — support decides. A driver cannot approve their own leave."""
        return self._decide(
            leave_id=leave_id,
            decider_principal_id=decider_principal_id,
            actor_may_decide=actor_may_decide,
            status=LeaveStatus.APPROVED,
            note=None,
        )

    def decline(
        self,
        *,
        leave_id: UUID,
        decider_principal_id: UUID,
        actor_may_decide: bool,
        note: str,
    ) -> LeaveRequest:
        """A decline must say why — the driver has to know what to do next."""
        if not note.strip():
            raise LeaveDecisionNoteRequired()
        return self._decide(
            leave_id=leave_id,
            decider_principal_id=decider_principal_id,
            actor_may_decide=actor_may_decide,
            status=LeaveStatus.DECLINED,
            note=note,
        )

    def withdraw(self, *, leave_id: UUID) -> LeaveRequest:
        self._uow.begin()
        try:
            request = self._load(leave_id)
            if not request.can_transition_to(LeaveStatus.WITHDRAWN):
                raise LeaveTransitionNotAllowed(
                    request.status.value, LeaveStatus.WITHDRAWN.value
                )
            request.status = LeaveStatus.WITHDRAWN
            request.decided_at = _now()
            request.version += 1
            self._uow.leave.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    def list_for_driver(self, *, driver_id: UUID) -> tuple[LeaveRequest, ...]:
        self._uow.begin()
        try:
            found = self._uow.leave.list_for_driver(driver_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def list_pending(self) -> tuple[LeaveRequest, ...]:
        self._uow.begin()
        try:
            found = self._uow.leave.list_pending()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    def covering(self, *, driver_id: UUID, day: date) -> LeaveRequest | None:
        self._uow.begin()
        try:
            found = self._uow.leave.find_covering(driver_id, day)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- internals

    def _decide(
        self,
        *,
        leave_id: UUID,
        decider_principal_id: UUID,
        actor_may_decide: bool,
        status: LeaveStatus,
        note: str | None,
    ) -> LeaveRequest:
        if not actor_may_decide:
            raise OnlySupportMayDecideLeave()

        self._uow.begin()
        try:
            request = self._load(leave_id)
            if not request.can_transition_to(status):
                raise LeaveTransitionNotAllowed(request.status.value, status.value)
            request.status = status
            request.decided_at = _now()
            request.decided_by_actor_id = decider_principal_id
            request.decision_note = (note or "").strip() or None
            request.version += 1
            self._uow.leave.save(request)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return request

    def _load(self, leave_id: UUID) -> LeaveRequest:
        request = self._uow.leave.get(leave_id)
        if request is None:
            raise LeaveRequestNotFound(str(leave_id))
        return request
