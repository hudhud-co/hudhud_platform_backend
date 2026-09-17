"""Driver-facing pickup task lifecycle.

Progress is forward-only and every step is bound to the assigned driver holding an
ONLINE pickup work session. Repeating a step the task already reached is an idempotent
replay, not an error — offline replay depends on that property.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pickup.application.history import record_history
from pickup.domain.entities import PickupTask, TaskHistoryEntry
from pickup.domain.errors import (
    ActingDriverMismatch,
    AssignmentAlreadyResolved,
    AssignmentNotAcknowledged,
    DriverNotAvailableForAssignment,
    ExceptionEvidenceInsufficient,
    InvalidTaskTransition,
    PackagingDecisionNotPermitted,
    PickupTaskAlreadyAccepted,
    PickupTaskNotAcceptable,
    PickupTaskNotFound,
    ScannedIdentifierMismatch,
    StopOutcomeAlreadyRecorded,
    StopOutcomeNotAllowed,
)
from pickup.domain.security import assignment_revision, fingerprint_identifier
from pickup.domain.value_objects import (
    ACTIVE_PICKUP_TASK_STATUSES,
    ASSESSMENT_REFUSAL_REASON,
    CONDITION_STATUS_TO_ASSESSMENT,
    DEFAULT_CONDITION_DECISION,
    PERMITTED_CONDITION_DECISIONS,
    PICKUP_TASK_PROGRESSION,
    AssignmentDeclineReason,
    AssignmentState,
    ConditionDecision,
    PackageConditionStatus,
    PackagingAssessment,
    PickupExceptionReason,
    PickupRefusalReason,
    PickupTaskStatus,
    StopOutcome,
)
from pickup.domain.value_objects import (
    CONTACT_ATTEMPT_REQUIRED_REASONS as _CONTACT_REQUIRED,
)
from pickup.domain.value_objects import (
    EVIDENCE_REQUIRED_REASONS as _EVIDENCE_REQUIRED,
)
from pickup.domain.workforce import DriverAvailabilityStatus
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import DriverWorkUnitOfWork

MAX_NOTE_LENGTH = 1024


@dataclass(frozen=True, slots=True)
class TaskCommand:
    pickup_task_id: UUID
    acting_driver_user_id: str
    occurred_at: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeclineAssignmentCommand(TaskCommand):
    reason: AssignmentDeclineReason | str = AssignmentDeclineReason.OTHER
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ScanTaskCommand(TaskCommand):
    scanned_identifier: str = ""


@dataclass(frozen=True, slots=True)
class CaptureProofCommand(TaskCommand):
    package_condition_status: PackageConditionStatus | str = PackageConditionStatus.GOOD
    condition_notes: str | None = None
    evidence_present: bool = False
    #: Optional. Omitted by clients that predate the packaging judgement, in which case it
    #: is derived from ``package_condition_status`` so their behaviour is unchanged.
    packaging_assessment: PackagingAssessment | str | None = None
    #: Optional. Omitted means "the decision this assessment implies".
    decision: ConditionDecision | str | None = None


@dataclass(frozen=True, slots=True)
class RefusePickupCommand(TaskCommand):
    reason: PickupRefusalReason | str = PickupRefusalReason.TOO_WEAK_PACKAGING
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ReportExceptionCommand(TaskCommand):
    reason: PickupExceptionReason | str = PickupExceptionReason.OTHER
    notes: str | None = None
    contact_attempted: bool = False
    contact_attempt_count: int = 0


@dataclass(frozen=True, slots=True)
class FailTaskCommand(TaskCommand):
    reason: PickupExceptionReason | str = PickupExceptionReason.OTHER
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class TaskLifecycleResult:
    task: PickupTask
    assignment_revision: str
    replayed: bool = False


class PickupTaskLifecycleService:
    """Assignment response and field progress for the assigned pickup driver."""

    def __init__(
        self,
        unit_of_work: DriverWorkUnitOfWork,
        *,
        manages_transaction: bool = True,
    ) -> None:
        self._uow = unit_of_work
        self._manages_transaction = manages_transaction

    def for_replay(self) -> PickupTaskLifecycleService:
        """Variant that runs inside a caller-owned transaction (offline replay)."""
        return PickupTaskLifecycleService(self._uow, manages_transaction=False)

    def _begin(self) -> None:
        if self._manages_transaction:
            self._uow.begin()

    def _commit(self) -> None:
        if self._manages_transaction:
            self._uow.commit()

    def _rollback(self) -> None:
        if self._manages_transaction:
            self._uow.rollback()

    def acknowledge(self, command: TaskCommand, *, actor: PickupActor) -> TaskLifecycleResult:
        self._begin()
        try:
            task = self._load_assigned(command)
            if task.assignment_state is AssignmentState.ACKNOWLEDGED:
                result = _result(task, replayed=True)
                self._commit()
                return result
            if task.assignment_state is AssignmentState.DECLINED:
                raise AssignmentAlreadyResolved(
                    pickup_task_id=str(task.pickup_task_id),
                    assignment_state=task.assignment_state.value,
                )
            self._require_online(command.acting_driver_user_id)
            task.assignment_state = AssignmentState.ACKNOWLEDGED
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action="assignment_acknowledged",
                previous_status=task.status.value,
                command=command,
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def decline(
        self, command: DeclineAssignmentCommand, *, actor: PickupActor
    ) -> TaskLifecycleResult:
        reason = _coerce_decline_reason(command.reason)
        self._begin()
        try:
            task = self._load_assigned(command)
            if task.assignment_state is AssignmentState.DECLINED:
                result = _result(task, replayed=True)
                self._commit()
                return result
            if task.assignment_state is AssignmentState.ACKNOWLEDGED:
                raise AssignmentAlreadyResolved(
                    pickup_task_id=str(task.pickup_task_id),
                    assignment_state=task.assignment_state.value,
                )
            previous = task.status.value
            task.assignment_state = AssignmentState.DECLINED
            task.declined_reason = reason
            task.declined_at = command.occurred_at
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action="assignment_declined",
                previous_status=previous,
                command=command,
                details={"reason": reason.value, "notes": _notes(command.notes)},
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def arrive(self, command: TaskCommand, *, actor: PickupActor) -> TaskLifecycleResult:
        return self._advance(
            command,
            actor=actor,
            target=PickupTaskStatus.ARRIVED,
            action="task_arrived",
            mutate=lambda task: setattr(task, "arrived_at", command.occurred_at),
        )

    def scan(self, command: ScanTaskCommand, *, actor: PickupActor) -> TaskLifecycleResult:
        identifier = (command.scanned_identifier or "").strip()
        if not identifier:
            raise ScannedIdentifierMismatch(pickup_task_id=str(command.pickup_task_id))

        def mutate(task: PickupTask) -> None:
            if task.scanned_identifier and task.scanned_identifier != identifier:
                raise ScannedIdentifierMismatch(pickup_task_id=str(task.pickup_task_id))
            task.scanned_identifier = identifier
            task.scanned_at = command.occurred_at

        return self._advance(
            command,
            actor=actor,
            target=PickupTaskStatus.SCANNED,
            action="task_scanned",
            mutate=mutate,
            details={"scanned_identifier_fingerprint": fingerprint_identifier(identifier)},
            replay_guard=lambda task: task.scanned_identifier == identifier,
        )

    def capture_proof(
        self, command: CaptureProofCommand, *, actor: PickupActor
    ) -> TaskLifecycleResult:
        condition = _coerce_condition(command.package_condition_status)
        if condition is not PackageConditionStatus.GOOD and not (
            command.evidence_present or (command.condition_notes or "").strip()
        ):
            raise ExceptionEvidenceInsufficient(
                reason=condition.value,
                required="condition notes or evidence",
            )
        assessment = _resolve_assessment(command.packaging_assessment, condition)
        decision = _resolve_decision(assessment, command.decision)

        def mutate(task: PickupTask) -> None:
            task.has_pickup_condition_proof = True
            task.condition_proof_captured_at = command.occurred_at
            task.package_condition_status = condition.value
            task.packaging_assessment = assessment
            task.condition_decision = decision

        return self._advance(
            command,
            actor=actor,
            target=PickupTaskStatus.PROOF_CAPTURED,
            action="condition_proof_captured",
            mutate=mutate,
            details={
                "package_condition_status": condition.value,
                "packaging_assessment": assessment.value,
                "condition_decision": decision.value,
                "evidence_present": command.evidence_present,
            },
            replay_guard=lambda task: task.has_pickup_condition_proof,
        )

    def report_exception(
        self, command: ReportExceptionCommand, *, actor: PickupActor
    ) -> TaskLifecycleResult:
        reason = _coerce_exception_reason(command.reason)
        _assert_exception_context(
            reason=reason,
            notes=command.notes,
            contact_attempted=command.contact_attempted,
            contact_attempt_count=command.contact_attempt_count,
            has_condition_proof=None,
        )
        self._begin()
        try:
            task = self._load_assigned(command)
            self._require_acknowledged(task)
            self._assert_not_closed(task)
            _assert_exception_context(
                reason=reason,
                notes=command.notes,
                contact_attempted=command.contact_attempted,
                contact_attempt_count=command.contact_attempt_count,
                has_condition_proof=task.has_pickup_condition_proof,
            )
            if (
                task.status is PickupTaskStatus.EXCEPTION_REPORTED
                and task.exception_reason is reason
            ):
                result = _result(task, replayed=True)
                self._commit()
                return result
            previous = task.status.value
            task.status = PickupTaskStatus.EXCEPTION_REPORTED
            task.exception_reason = reason
            task.exception_reported_at = command.occurred_at
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action="exception_reported",
                previous_status=previous,
                command=command,
                details={
                    "reason": reason.value,
                    "notes": _notes(command.notes),
                    "contact_attempted": command.contact_attempted,
                    "contact_attempt_count": command.contact_attempt_count,
                },
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def fail(self, command: FailTaskCommand, *, actor: PickupActor) -> TaskLifecycleResult:
        reason = _coerce_exception_reason(command.reason)
        self._begin()
        try:
            task = self._load_assigned(command)
            self._require_acknowledged(task)
            self._assert_not_closed(task)
            if task.status is PickupTaskStatus.FAILED:
                result = _result(task, replayed=True)
                self._commit()
                return result
            previous = task.status.value
            task.status = PickupTaskStatus.FAILED
            task.exception_reason = reason
            task.failed_at = command.occurred_at
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action="task_failed",
                previous_status=previous,
                command=command,
                details={"reason": reason.value, "notes": _notes(command.notes)},
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def refuse(
        self, command: RefusePickupCommand, *, actor: PickupActor
    ) -> TaskLifecycleResult:
        """Refuse one parcel at the door. It stays with the merchant.

        Nothing enters HUDHUD custody and nothing is published — "The parcel stays with the
        merchant. No custody event was recorded." (Driver App v8 ``notAccepted``).
        """
        reason = _coerce_refusal_reason(command.reason)
        return self._record_stop_outcome(
            command,
            actor=actor,
            outcome=StopOutcome.REFUSED,
            reason=reason,
            action="pickup_refused",
            details={"reason": reason.value, "notes": _notes(command.notes)},
        )

    def mark_not_presented(
        self, command: TaskCommand, *, actor: PickupActor
    ) -> TaskLifecycleResult:
        """The merchant did not hand this parcel over.

        It stays expected and unpicked, with no penalty for anyone (Driver App v8
        ``progress`` not-presented sheet). Like refusal, it starts no custody.
        """
        return self._record_stop_outcome(
            command,
            actor=actor,
            outcome=StopOutcome.NOT_PRESENTED,
            reason=None,
            action="pickup_not_presented",
            details=None,
        )

    def _record_stop_outcome(
        self,
        command: TaskCommand,
        *,
        actor: PickupActor,
        outcome: StopOutcome,
        reason: PickupRefusalReason | None,
        action: str,
        details: dict[str, object] | None,
    ) -> TaskLifecycleResult:
        self._begin()
        try:
            task = self._load_assigned(command)
            self._require_acknowledged(task)
            if task.is_accepted:
                # Acceptance is the one custody event; it can never be walked back here.
                raise StopOutcomeNotAllowed(
                    pickup_task_id=str(task.pickup_task_id),
                    reason="already accepted into custody",
                )
            self._assert_not_closed(task)
            if task.stop_outcome is not None:
                if task.stop_outcome is outcome and task.stop_outcome_reason == reason:
                    result = _result(task, replayed=True)
                    self._commit()
                    return result
                raise StopOutcomeAlreadyRecorded(
                    pickup_task_id=str(task.pickup_task_id),
                    stop_outcome=task.stop_outcome.value,
                )
            previous = task.status.value
            task.status = PickupTaskStatus.FAILED
            task.stop_outcome = outcome
            task.stop_outcome_reason = reason
            task.stop_outcome_at = command.occurred_at
            task.failed_at = command.occurred_at
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action=action,
                previous_status=previous,
                command=command,
                details=details,
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def _advance(
        self,
        command: TaskCommand,
        *,
        actor: PickupActor,
        target: PickupTaskStatus,
        action: str,
        mutate,
        details: dict[str, object] | None = None,
        replay_guard=None,
    ) -> TaskLifecycleResult:
        self._begin()
        try:
            task = self._load_assigned(command)
            self._require_acknowledged(task)
            self._assert_not_closed(task)
            current_rank = _rank(task.status)
            target_rank = _rank(target)
            if current_rank >= target_rank:
                if replay_guard is not None and not replay_guard(task):
                    raise InvalidTaskTransition(
                        pickup_task_id=str(task.pickup_task_id),
                        current_status=task.status.value,
                        target_status=target.value,
                    )
                result = _result(task, replayed=True)
                self._commit()
                return result
            if target_rank - current_rank != 1:
                raise InvalidTaskTransition(
                    pickup_task_id=str(task.pickup_task_id),
                    current_status=task.status.value,
                    target_status=target.value,
                )
            previous = task.status.value
            mutate(task)
            task.status = target
            task.version += 1
            result = self._persist(
                task,
                actor=actor,
                action=action,
                previous_status=previous,
                command=command,
                details=details,
            )
        except Exception:
            self._rollback()
            raise
        self._commit()
        return result

    def _persist(
        self,
        task: PickupTask,
        *,
        actor: PickupActor,
        action: str,
        previous_status: str,
        command: TaskCommand,
        details: dict[str, object] | None = None,
    ) -> TaskLifecycleResult:
        self._uow.pickup_tasks.save_pickup_task(task)
        record_history(
            self._uow.task_history,
            pickup_task_id=task.pickup_task_id,
            action=action,
            actor=actor,
            previous_status=previous_status,
            new_status=task.status.value,
            occurred_at=command.occurred_at,
            request_id=command.request_id,
            details={
                "assignment_state": task.assignment_state.value,
                "shipment_id": str(task.shipment_id),
                **(details or {}),
            },
        )
        return _result(task)

    def list_history(self, pickup_task_id: UUID) -> tuple[TaskHistoryEntry, ...]:
        """Append-only audit trail for one pickup task, oldest first."""
        return self._uow.task_history.list_entries_for_task(pickup_task_id)

    def _load_assigned(self, command: TaskCommand) -> PickupTask:
        task = self._uow.pickup_tasks.get_pickup_task(command.pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(command.pickup_task_id))
        if task.assigned_driver_user_id != command.acting_driver_user_id:
            raise ActingDriverMismatch(
                pickup_task_id=str(task.pickup_task_id),
                acting_driver_user_id=command.acting_driver_user_id,
            )
        return task

    def _require_acknowledged(self, task: PickupTask) -> None:
        if task.assignment_state is not AssignmentState.ACKNOWLEDGED:
            raise AssignmentNotAcknowledged(
                pickup_task_id=str(task.pickup_task_id),
                assignment_state=task.assignment_state.value,
            )

    def _assert_not_closed(self, task: PickupTask) -> None:
        if task.is_accepted:
            assert task.acceptance_state is not None
            raise PickupTaskAlreadyAccepted(
                pickup_task_id=str(task.pickup_task_id),
                acceptance_state=task.acceptance_state.value,
            )
        if task.is_terminal:
            raise PickupTaskNotAcceptable(
                pickup_task_id=str(task.pickup_task_id),
                status=task.status.value,
            )

    def _require_online(self, driver_user_id: str) -> None:
        session = self._uow.work_sessions.get_open_session_for_driver(driver_user_id)
        if session is None or not session.can_receive_assignment:
            availability = (
                session.availability.value
                if session is not None
                else DriverAvailabilityStatus.OFFLINE.value
            )
            raise DriverNotAvailableForAssignment(
                driver_user_id=driver_user_id,
                availability=availability,
            )


def _result(task: PickupTask, *, replayed: bool = False) -> TaskLifecycleResult:
    return TaskLifecycleResult(
        task=task,
        assignment_revision=current_assignment_revision(task),
        replayed=replayed,
    )


def current_assignment_revision(task: PickupTask) -> str:
    return assignment_revision(
        pickup_task_id=task.pickup_task_id,
        assigned_driver_user_id=task.assigned_driver_user_id,
        assigned_batch_id=task.assigned_batch_id,
        attempt_number=task.attempt_number,
        assignment_state=task.assignment_state.value,
        superseded_by_task_id=task.superseded_by_task_id,
    )


def _rank(status: PickupTaskStatus) -> int:
    try:
        return PICKUP_TASK_PROGRESSION.index(status)
    except ValueError:
        # EXCEPTION_REPORTED keeps the task recoverable but off the forward path.
        if status in ACTIVE_PICKUP_TASK_STATUSES:
            return 0
        return len(PICKUP_TASK_PROGRESSION)


def _coerce_decline_reason(
    value: AssignmentDeclineReason | str,
) -> AssignmentDeclineReason:
    if isinstance(value, AssignmentDeclineReason):
        return value
    try:
        return AssignmentDeclineReason(str(value).strip().upper())
    except ValueError as exc:
        raise ExceptionEvidenceInsufficient(
            reason=str(value),
            required="a known decline reason",
        ) from exc


def _coerce_exception_reason(
    value: PickupExceptionReason | str,
) -> PickupExceptionReason:
    if isinstance(value, PickupExceptionReason):
        return value
    try:
        return PickupExceptionReason(str(value).strip().upper())
    except ValueError as exc:
        raise ExceptionEvidenceInsufficient(
            reason=str(value),
            required="a known exception reason",
        ) from exc


def _coerce_condition(
    value: PackageConditionStatus | str,
) -> PackageConditionStatus:
    if isinstance(value, PackageConditionStatus):
        return value
    try:
        return PackageConditionStatus(str(value).strip().upper())
    except ValueError as exc:
        raise ExceptionEvidenceInsufficient(
            reason=str(value),
            required="a known package condition status",
        ) from exc


def _coerce_refusal_reason(
    value: PickupRefusalReason | str,
) -> PickupRefusalReason:
    if isinstance(value, PickupRefusalReason):
        return value
    try:
        return PickupRefusalReason(str(value).strip().upper())
    except ValueError as exc:
        raise ExceptionEvidenceInsufficient(
            reason=str(value),
            required="a known pickup refusal reason",
        ) from exc


def _resolve_assessment(
    value: PackagingAssessment | str | None,
    condition: PackageConditionStatus,
) -> PackagingAssessment:
    """Take the driver's judgement, or derive it from the condition status.

    Derivation keeps every caller that predates the packaging judgement — and every row
    already stored — behaving exactly as before.
    """
    if value is None:
        return CONDITION_STATUS_TO_ASSESSMENT.get(condition.value, PackagingAssessment.GOOD)
    if isinstance(value, PackagingAssessment):
        return value
    try:
        return PackagingAssessment(str(value).strip().upper())
    except ValueError as exc:
        raise ExceptionEvidenceInsufficient(
            reason=str(value),
            required="a known packaging assessment",
        ) from exc


def _resolve_decision(
    assessment: PackagingAssessment,
    value: ConditionDecision | str | None,
) -> ConditionDecision:
    """Apply the permitted-decision table for this assessment.

    ``TOO_WEAK`` is the invariant that matters: packaging that will not survive handling
    can only be refused.
    """
    if value is None:
        return DEFAULT_CONDITION_DECISION[assessment]
    if isinstance(value, ConditionDecision):
        decision = value
    else:
        try:
            decision = ConditionDecision(str(value).strip().upper())
        except ValueError as exc:
            raise ExceptionEvidenceInsufficient(
                reason=str(value),
                required="a known condition decision",
            ) from exc
    permitted = PERMITTED_CONDITION_DECISIONS[assessment]
    if decision not in permitted:
        raise PackagingDecisionNotPermitted(
            assessment=assessment.value,
            decision=decision.value,
            permitted=tuple(sorted(d.value for d in permitted)),
        )
    return decision


def refusal_reason_for_assessment(
    assessment: PackagingAssessment,
) -> PickupRefusalReason:
    """Refusal reason implied when the driver refuses straight from the condition check."""
    return ASSESSMENT_REFUSAL_REASON.get(
        assessment, PickupRefusalReason.DOES_NOT_MATCH_SHIPMENT
    )


def _assert_exception_context(
    *,
    reason: PickupExceptionReason,
    notes: str | None,
    contact_attempted: bool,
    contact_attempt_count: int,
    has_condition_proof: bool | None,
) -> None:
    """Minimum accepted evidence per reason — mirrors the Driver exception contract."""
    if reason in _CONTACT_REQUIRED and not (contact_attempted and contact_attempt_count >= 1):
        raise ExceptionEvidenceInsufficient(
            reason=reason.value,
            required="contact_attempted with at least one attempt",
        )
    if reason not in _EVIDENCE_REQUIRED:
        return
    if (notes or "").strip():
        return
    if has_condition_proof is None:
        # Task state not loaded yet; the post-load check enforces the rule.
        return
    if reason is PickupExceptionReason.SENDER_ABSENT and contact_attempted:
        return
    if has_condition_proof:
        return
    raise ExceptionEvidenceInsufficient(
        reason=reason.value,
        required="notes or an existing condition proof",
    )


def _notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    trimmed = notes.strip()
    return trimmed[:MAX_NOTE_LENGTH] if trimmed else None
