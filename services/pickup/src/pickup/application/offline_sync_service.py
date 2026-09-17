"""Offline work authorization, append-only replay, and reconciliation.

Guarantees this service provides:

* **No undownloaded work.** A capture is only replayable under a signed, device-bound,
  time-boxed authorization naming the exact resource and assignment revision.
* **Nothing is lost.** Every submission is preserved, applied or not. Rejections and
  conflicts become Operations-owned reconciliation cases, never silent drops.
* **Exactly-once effect.** `operation_id` is the client's idempotency key; a replay with
  identical content returns the stored outcome, and reuse with different content is a
  preserved conflict.
* **Order is authority.** Commands apply only in contiguous sequence; a gap opens a case
  and the batch keeps going, so one bad capture cannot block the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pickup.application.history import record_history
from pickup.application.offline_dispatch import (
    DeferredOfflineOperation,
    InvalidOfflinePayload,
    OfflineCommandContext,
    OfflineOperationDispatcher,
)
from pickup.application.task_lifecycle_service import current_assignment_revision
from pickup.domain.entities import PickupTask
from pickup.domain.errors import (
    ActingDriverMismatch,
    DriverNotAvailableForAssignment,
    OfflineAuthorizationInvalid,
    OfflineAuthorizationNotFound,
    OfflineAuthorizationRevoked,
    OfflineBatchTooLarge,
    OfflineOperationNotAuthorized,
    OfflineSyncDeadlinePassed,
    PickupError,
    PickupTaskNotAcceptable,
    PickupTaskNotFound,
    ReconciliationCaseAlreadyResolved,
    ReconciliationCaseNotFound,
)
from pickup.domain.offline import (
    CUSTODY_OFFLINE_OPERATIONS,
    OfflineAuthorization,
    OfflineEventRecord,
    OfflineEventStatus,
    OfflineOperation,
    OfflineOutcomeCode,
    OfflineReconciliationCase,
    OfflineResourceType,
    OfflineStream,
    ReconciliationCaseStatus,
    ReconciliationResolution,
)
from pickup.domain.sanitize import sanitize_error_message
from pickup.domain.security import (
    InvalidOfflineToken,
    decode_offline_token,
    encode_offline_token,
    hash_device_id,
    hash_offline_token,
    offline_event_fingerprint,
)
from pickup.ports.authorization import PickupActor
from pickup.ports.repository import OfflineUnitOfWork

#: Tolerated clock skew between a driver device and the server.
CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class OfflinePolicy:
    authorization_ttl_minutes: int = 720
    sync_grace_days: int = 3
    max_sync_events: int = 200


@dataclass(frozen=True, slots=True)
class IssueOfflineAuthorizationCommand:
    driver_user_id: str
    device_id: str
    resource_id: UUID
    occurred_at: datetime
    requested_operations: tuple[OfflineOperation, ...] = ()
    resource_type: OfflineResourceType = OfflineResourceType.PICKUP_TASK
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedOfflineAuthorization:
    authorization: OfflineAuthorization
    token: str


@dataclass(frozen=True, slots=True)
class RevokeOfflineAuthorizationCommand:
    authorization_id: UUID
    driver_user_id: str
    occurred_at: datetime
    reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class OfflineEventSubmission:
    operation_id: UUID
    sequence: int
    operation: OfflineOperation | str
    resource_id: UUID
    assignment_revision: str
    captured_at: datetime
    payload_fingerprint: str
    resource_type: OfflineResourceType = OfflineResourceType.PICKUP_TASK
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SyncOfflineWorkCommand:
    driver_user_id: str
    device_id: str
    stream_id: UUID
    authorization_token: str
    events: tuple[OfflineEventSubmission, ...]
    occurred_at: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    event_row_id: UUID
    operation_id: UUID
    sequence: int
    status: OfflineEventStatus
    outcome_code: OfflineOutcomeCode
    detail: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class SyncResult:
    stream_id: UUID
    authorization_id: UUID
    last_contiguous_sequence: int
    outcomes: tuple[SyncOutcome, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "received": len(self.outcomes),
            "applied": sum(
                outcome.status is OfflineEventStatus.APPLIED for outcome in self.outcomes
            ),
            "reconciliation_required": sum(
                outcome.status is OfflineEventStatus.RECONCILIATION_REQUIRED
                for outcome in self.outcomes
            ),
            "rejected": sum(
                outcome.status is OfflineEventStatus.REJECTED for outcome in self.outcomes
            ),
            "replayed": sum(outcome.replayed for outcome in self.outcomes),
        }


@dataclass(frozen=True, slots=True)
class ResolveReconciliationCommand:
    case_id: UUID
    resolution: ReconciliationResolution | str
    occurred_at: datetime
    notes: str | None = None
    request_id: str | None = None


class OfflineSyncService:
    """Issue offline authority, replay captured work, and open reconciliation cases."""

    def __init__(
        self,
        unit_of_work: OfflineUnitOfWork,
        dispatcher: OfflineOperationDispatcher,
        *,
        signing_key: str,
        policy: OfflinePolicy | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._dispatcher = dispatcher
        self._signing_key = signing_key
        self._policy = policy or OfflinePolicy()

    # -------------------------------------------------------------- authorization

    def issue_authorization(
        self, command: IssueOfflineAuthorizationCommand, *, actor: PickupActor
    ) -> IssuedOfflineAuthorization:
        self._uow.begin()
        try:
            task = self._load_task(command.resource_id)
            if task.assigned_driver_user_id != command.driver_user_id:
                raise ActingDriverMismatch(
                    pickup_task_id=str(task.pickup_task_id),
                    acting_driver_user_id=command.driver_user_id,
                )
            if task.is_terminal or task.is_accepted:
                raise PickupTaskNotAcceptable(
                    pickup_task_id=str(task.pickup_task_id),
                    status=task.status.value,
                )
            session = self._uow.work_sessions.get_open_session_for_driver(
                command.driver_user_id
            )
            if session is None or not session.can_receive_assignment:
                # Downloading work is taking work: it needs the same availability.
                raise DriverNotAvailableForAssignment(
                    driver_user_id=command.driver_user_id,
                    availability=session.availability.value if session else "OFFLINE",
                )

            allowed = _allowed_operations(actor)
            requested = tuple(command.requested_operations or allowed)
            invalid = tuple(sorted({item.value for item in requested} - {i.value for i in allowed}))
            if invalid:
                raise OfflineOperationNotAuthorized(operations=invalid)
            if not requested:
                raise OfflineOperationNotAuthorized(operations=())

            authorization_id = uuid4()
            issued_at = _as_utc(command.occurred_at)
            expires_at = issued_at + timedelta(
                minutes=self._policy.authorization_ttl_minutes
            )
            sync_deadline = expires_at + timedelta(days=self._policy.sync_grace_days)
            device_hash = hash_device_id(command.device_id)
            revision = current_assignment_revision(task)
            token = encode_offline_token(
                {
                    "jti": str(authorization_id),
                    "sub": command.driver_user_id,
                    "dev": device_hash,
                    "rt": command.resource_type.value,
                    "rid": str(task.pickup_task_id),
                    "rev": revision,
                    "ops": sorted(item.value for item in requested),
                    "iat": int(issued_at.timestamp()),
                    "exp": int(expires_at.timestamp()),
                    "sd": int(sync_deadline.timestamp()),
                },
                signing_key=self._signing_key,
            )
            authorization = OfflineAuthorization(
                authorization_id=authorization_id,
                driver_user_id=command.driver_user_id,
                device_id_hash=device_hash,
                resource_type=command.resource_type,
                resource_id=task.pickup_task_id,
                assignment_revision=revision,
                permitted_operations=tuple(sorted(requested, key=lambda item: item.value)),
                token_hash=hash_offline_token(token),
                issued_at=issued_at,
                expires_at=expires_at,
                sync_deadline=sync_deadline,
                resource_snapshot=_snapshot(task, revision),
            )
            self._uow.offline_authorizations.save_authorization(authorization)
            record_history(
                self._uow.task_history,
                pickup_task_id=task.pickup_task_id,
                action="offline_authorization_issued",
                actor=actor,
                previous_status=task.status.value,
                new_status=task.status.value,
                occurred_at=issued_at,
                request_id=command.request_id,
                details={
                    "authorization_id": str(authorization_id),
                    "device_id_hash": device_hash,
                    "assignment_revision": revision,
                    "permitted_operations": ",".join(
                        item.value for item in authorization.permitted_operations
                    ),
                    "expires_at": expires_at.isoformat(),
                    "sync_deadline": sync_deadline.isoformat(),
                },
            )
            issued = IssuedOfflineAuthorization(authorization=authorization, token=token)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return issued

    def revoke_authorization(
        self, command: RevokeOfflineAuthorizationCommand, *, actor: PickupActor
    ) -> OfflineAuthorization:
        self._uow.begin()
        try:
            authorization = self._uow.offline_authorizations.get_authorization(
                command.authorization_id
            )
            if authorization is None or authorization.driver_user_id != command.driver_user_id:
                raise OfflineAuthorizationNotFound()
            if authorization.is_revoked:
                self._uow.commit()
                return authorization
            authorization.revoked_at = _as_utc(command.occurred_at)
            authorization.revoked_reason = command.reason
            self._uow.offline_authorizations.save_authorization(authorization)
            record_history(
                self._uow.task_history,
                pickup_task_id=authorization.resource_id,
                action="offline_authorization_revoked",
                actor=actor,
                previous_status=None,
                new_status=None,
                occurred_at=authorization.revoked_at,
                request_id=command.request_id,
                details={
                    "authorization_id": str(authorization.authorization_id),
                    "reason": command.reason,
                },
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return authorization

    # ---------------------------------------------------------------------- sync

    def sync(self, command: SyncOfflineWorkCommand, *, actor: PickupActor) -> SyncResult:
        if len(command.events) > self._policy.max_sync_events:
            raise OfflineBatchTooLarge(max_events=self._policy.max_sync_events)

        claims = self._decode_claims(command)
        now = _as_utc(command.occurred_at)
        self._uow.begin()
        try:
            authorization = self._load_bound_authorization(command, claims)
            stream = self._load_or_create_stream(command, authorization)
            outcomes: list[SyncOutcome] = []
            for submission in command.events:
                outcomes.append(
                    self._process_submission(
                        submission=submission,
                        authorization=authorization,
                        stream=stream,
                        actor=actor,
                        now=now,
                        request_id=command.request_id,
                    )
                )
            stream.last_received_at = now
            self._uow.offline_streams.save_stream(stream)
            result = SyncResult(
                stream_id=stream.stream_id,
                authorization_id=authorization.authorization_id,
                last_contiguous_sequence=stream.last_contiguous_sequence,
                outcomes=tuple(outcomes),
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return result

    def _process_submission(
        self,
        *,
        submission: OfflineEventSubmission,
        authorization: OfflineAuthorization,
        stream: OfflineStream,
        actor: PickupActor,
        now: datetime,
        request_id: str | None,
    ) -> SyncOutcome:
        operation = _coerce_operation(submission.operation)
        existing = self._uow.offline_events.find_by_operation_id(
            driver_user_id=authorization.driver_user_id,
            operation_id=submission.operation_id,
        )
        if existing is not None:
            return self._handle_known_operation(
                existing=existing,
                submission=submission,
                operation=operation,
                authorization=authorization,
                stream=stream,
                actor=actor,
                now=now,
                request_id=request_id,
            )

        reused = self._uow.offline_events.find_by_stream_sequence(
            stream_id=stream.stream_id,
            sequence=submission.sequence,
        )
        if reused is not None:
            self._attach_conflict(reused, submission, OfflineOutcomeCode.SEQUENCE_REUSE)
            return SyncOutcome(
                event_row_id=reused.event_row_id,
                operation_id=submission.operation_id,
                sequence=submission.sequence,
                status=OfflineEventStatus.REJECTED,
                outcome_code=OfflineOutcomeCode.SEQUENCE_REUSE,
                detail="stream sequence was already used by a different operation",
                replayed=True,
            )

        issue = self._validate(
            submission=submission,
            operation=operation,
            authorization=authorization,
            stream=stream,
            now=now,
        )
        if issue is not None:
            status, code, detail, snapshot = issue
            record = self._preserve(
                submission=submission,
                operation=operation,
                authorization=authorization,
                stream=stream,
                status=status,
                code=code,
                outcome={"message": detail},
                now=now,
            )
            if status is OfflineEventStatus.RECONCILIATION_REQUIRED:
                self._open_case(record, submission, code, detail, snapshot)
            if submission.sequence == stream.last_contiguous_sequence + 1:
                # The gap is recorded; the client may resend the missing command later.
                stream.last_contiguous_sequence = submission.sequence
            return _outcome(record, detail=detail)

        return self._replay(
            submission=submission,
            operation=operation,
            authorization=authorization,
            stream=stream,
            actor=actor,
            now=now,
        )

    def _replay(
        self,
        *,
        submission: OfflineEventSubmission,
        operation: OfflineOperation,
        authorization: OfflineAuthorization,
        stream: OfflineStream,
        actor: PickupActor,
        now: datetime,
        existing: OfflineEventRecord | None = None,
    ) -> SyncOutcome:
        context = OfflineCommandContext(
            driver_user_id=authorization.driver_user_id,
            operation_id=submission.operation_id,
            resource_id=submission.resource_id,
            captured_at=submission.captured_at,
            payload=submission.payload,
        )
        status = OfflineEventStatus.APPLIED
        code = OfflineOutcomeCode.APPLIED
        outcome: dict[str, Any] = {}
        detail = "applied"
        try:
            with self._uow.savepoint():
                result = self._dispatcher.dispatch(
                    operation=operation,
                    context=context,
                    actor=actor,
                )
            outcome = {
                "status": result.task.status.value,
                "assignment_revision": result.assignment_revision,
                "idempotent_replay": result.replayed,
            }
        except DeferredOfflineOperation as exc:
            status = OfflineEventStatus.RECONCILIATION_REQUIRED
            code = OfflineOutcomeCode.OWNING_WORKFLOW_REQUIRED
            detail = exc.detail
            outcome = {"message": detail}
        except (PickupError, InvalidOfflinePayload, ValueError, TypeError) as exc:
            status = OfflineEventStatus.RECONCILIATION_REQUIRED
            code = OfflineOutcomeCode.DOMAIN_REJECTED
            detail = sanitize_error_message(str(exc))
            outcome = {"message": detail}

        if existing is not None:
            existing.status = status
            existing.outcome_code = code
            existing.outcome = {"previous_outcome": existing.outcome, **outcome}
            existing.processed_at = now
            existing.replayed = True
            self._uow.offline_events.update_event(existing)
            record = existing
        else:
            record = self._preserve(
                submission=submission,
                operation=operation,
                authorization=authorization,
                stream=stream,
                status=status,
                code=code,
                outcome=outcome,
                now=now,
            )
        if status is OfflineEventStatus.RECONCILIATION_REQUIRED:
            snapshot = self._current_snapshot(submission.resource_id)
            self._open_case(record, submission, code, detail, snapshot)
        stream.last_contiguous_sequence = submission.sequence
        return _outcome(record, detail=detail)

    def _handle_known_operation(
        self,
        *,
        existing: OfflineEventRecord,
        submission: OfflineEventSubmission,
        operation: OfflineOperation,
        authorization: OfflineAuthorization,
        stream: OfflineStream,
        actor: PickupActor,
        now: datetime,
        request_id: str | None,
    ) -> SyncOutcome:
        _ = request_id
        if existing.payload_fingerprint != submission.payload_fingerprint:
            # Same operation id, different content: preserve both, apply neither.
            self._attach_conflict(
                existing, submission, OfflineOutcomeCode.OPERATION_ID_REUSE
            )
            return SyncOutcome(
                event_row_id=existing.event_row_id,
                operation_id=submission.operation_id,
                sequence=submission.sequence,
                status=OfflineEventStatus.REJECTED,
                outcome_code=OfflineOutcomeCode.OPERATION_ID_REUSE,
                detail="operation id was reused with different content",
                replayed=True,
            )

        resumable_gap = (
            existing.stream_id == stream.stream_id
            and existing.outcome_code is OfflineOutcomeCode.SEQUENCE_GAP
            and existing.sequence == stream.last_contiguous_sequence + 1
        )
        if resumable_gap:
            case = self._uow.reconciliation_cases.get_open_case_for_event(
                existing.event_row_id
            )
            if case is not None:
                issue = self._validate(
                    submission=submission,
                    operation=operation,
                    authorization=authorization,
                    stream=stream,
                    now=now,
                )
                if issue is None:
                    outcome = self._replay(
                        submission=submission,
                        operation=operation,
                        authorization=authorization,
                        stream=stream,
                        actor=actor,
                        now=now,
                        existing=existing,
                    )
                    if outcome.status is OfflineEventStatus.APPLIED:
                        case.status = ReconciliationCaseStatus.RESOLVED
                        case.resolved_at = now
                        case.resolution = (
                            ReconciliationResolution.ORDER_RESTORED_AND_APPLIED
                        )
                        case.resolution_notes = (
                            "the missing earlier sequence arrived and this exact "
                            "capture was then applied by its owning workflow"
                        )
                        self._uow.reconciliation_cases.save_case(case)
                    return SyncOutcome(
                        event_row_id=outcome.event_row_id,
                        operation_id=outcome.operation_id,
                        sequence=outcome.sequence,
                        status=outcome.status,
                        outcome_code=outcome.outcome_code,
                        detail=outcome.detail,
                        replayed=True,
                    )

        return SyncOutcome(
            event_row_id=existing.event_row_id,
            operation_id=existing.operation_id,
            sequence=existing.sequence,
            status=existing.status,
            outcome_code=existing.outcome_code,
            detail=str(existing.outcome.get("message", "stored outcome returned")),
            replayed=True,
        )

    def _validate(
        self,
        *,
        submission: OfflineEventSubmission,
        operation: OfflineOperation,
        authorization: OfflineAuthorization,
        stream: OfflineStream,
        now: datetime,
    ) -> tuple[OfflineEventStatus, OfflineOutcomeCode, str, dict[str, Any]] | None:
        expected = stream.last_contiguous_sequence + 1
        if submission.sequence != expected:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.SEQUENCE_GAP,
                f"expected sequence {expected}, received {submission.sequence}",
                authorization.resource_snapshot,
            )

        expected_fingerprint = offline_event_fingerprint(
            operation_id=submission.operation_id,
            sequence=submission.sequence,
            operation=operation.value,
            resource_type=submission.resource_type.value,
            resource_id=submission.resource_id,
            assignment_revision_value=submission.assignment_revision,
            captured_at=submission.captured_at,
            payload=submission.payload,
        )
        if expected_fingerprint != submission.payload_fingerprint:
            return (
                OfflineEventStatus.REJECTED,
                OfflineOutcomeCode.PAYLOAD_FINGERPRINT_MISMATCH,
                "payload fingerprint does not match the canonical capture body",
                authorization.resource_snapshot,
            )
        if (
            submission.resource_type is not authorization.resource_type
            or submission.resource_id != authorization.resource_id
        ):
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.AUTHORIZATION_SCOPE_MISMATCH,
                "capture is outside the authorization scope",
                authorization.resource_snapshot,
            )
        if operation not in authorization.permitted_operations:
            return (
                OfflineEventStatus.REJECTED,
                OfflineOutcomeCode.OPERATION_NOT_AUTHORIZED,
                "operation is not present in the signed authorization",
                authorization.resource_snapshot,
            )

        captured_at = _as_utc(submission.captured_at)
        if captured_at < authorization.issued_at - CLOCK_SKEW:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.CAPTURE_BEFORE_AUTHORIZATION,
                "capture time predates this work authorization",
                authorization.resource_snapshot,
            )
        if captured_at > authorization.expires_at:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.CAPTURE_AFTER_AUTHORIZATION_EXPIRY,
                "capture happened after offline authority expired",
                authorization.resource_snapshot,
            )
        if captured_at > now + CLOCK_SKEW:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.CAPTURE_TIME_IN_FUTURE,
                "capture time exceeds the allowed clock skew",
                authorization.resource_snapshot,
            )

        task = self._uow.pickup_tasks.get_pickup_task(submission.resource_id)
        if task is None or task.assigned_driver_user_id != authorization.driver_user_id:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.ASSIGNMENT_NO_LONGER_OWNED,
                "the work is no longer assigned to this driver",
                authorization.resource_snapshot,
            )
        revision = current_assignment_revision(task)
        if revision != submission.assignment_revision:
            return (
                OfflineEventStatus.RECONCILIATION_REQUIRED,
                OfflineOutcomeCode.STALE_ASSIGNMENT_REVISION,
                "the server assignment revision changed after download",
                _snapshot(task, revision),
            )
        return None

    # --------------------------------------------------------------- persistence

    def _preserve(
        self,
        *,
        submission: OfflineEventSubmission,
        operation: OfflineOperation,
        authorization: OfflineAuthorization,
        stream: OfflineStream,
        status: OfflineEventStatus,
        code: OfflineOutcomeCode,
        outcome: dict[str, Any],
        now: datetime,
    ) -> OfflineEventRecord:
        record = OfflineEventRecord(
            event_row_id=uuid4(),
            stream_id=stream.stream_id,
            authorization_id=authorization.authorization_id,
            driver_user_id=authorization.driver_user_id,
            operation_id=submission.operation_id,
            sequence=submission.sequence,
            operation=operation,
            resource_type=submission.resource_type,
            resource_id=submission.resource_id,
            assignment_revision=submission.assignment_revision,
            captured_at=_as_utc(submission.captured_at),
            payload_fingerprint=submission.payload_fingerprint,
            payload=dict(submission.payload),
            status=status,
            outcome_code=code,
            outcome=outcome,
            processed_at=now,
        )
        self._uow.offline_events.append_event(record)
        return record

    def _open_case(
        self,
        record: OfflineEventRecord,
        submission: OfflineEventSubmission,
        code: OfflineOutcomeCode,
        detail: str,
        snapshot: dict[str, Any],
    ) -> None:
        existing = self._uow.reconciliation_cases.get_open_case_for_event(
            record.event_row_id
        )
        if existing is not None:
            existing.reason_code = code
            existing.reason_detail = detail
            existing.authoritative_state = snapshot
            self._uow.reconciliation_cases.save_case(existing)
            return
        self._uow.reconciliation_cases.save_case(
            OfflineReconciliationCase(
                case_id=uuid4(),
                offline_event_row_id=record.event_row_id,
                driver_user_id=record.driver_user_id,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                status=ReconciliationCaseStatus.OPEN,
                reason_code=code,
                reason_detail=detail,
                authoritative_state=snapshot,
                submitted_event=_submission_json(submission),
                custody_implication=record.operation in CUSTODY_OFFLINE_OPERATIONS,
                opened_at=record.processed_at,
            )
        )

    def _attach_conflict(
        self,
        record: OfflineEventRecord,
        submission: OfflineEventSubmission,
        code: OfflineOutcomeCode,
    ) -> None:
        """Preserve a conflicting replay against the original capture. Nothing is lost."""
        case = self._uow.reconciliation_cases.get_open_case_for_event(record.event_row_id)
        if case is None:
            self._uow.reconciliation_cases.save_case(
                OfflineReconciliationCase(
                    case_id=uuid4(),
                    offline_event_row_id=record.event_row_id,
                    driver_user_id=record.driver_user_id,
                    resource_type=record.resource_type,
                    resource_id=record.resource_id,
                    status=ReconciliationCaseStatus.OPEN,
                    reason_code=code,
                    reason_detail=f"{code.value} against the stored capture",
                    authoritative_state=self._current_snapshot(record.resource_id),
                    submitted_event={
                        "conflicting_replays": [_submission_json(submission)],
                    },
                    custody_implication=record.operation in CUSTODY_OFFLINE_OPERATIONS,
                    opened_at=record.processed_at,
                )
            )
            return
        conflicts = list(case.submitted_event.get("conflicting_replays") or [])
        conflicts.append(_submission_json(submission))
        case.submitted_event = {**case.submitted_event, "conflicting_replays": conflicts}
        self._uow.reconciliation_cases.save_case(case)

    # ------------------------------------------------------------ reconciliation

    def resolve_case(
        self, command: ResolveReconciliationCommand, *, actor: PickupActor
    ) -> OfflineReconciliationCase:
        resolution = _coerce_resolution(command.resolution)
        self._uow.begin()
        try:
            case = self._uow.reconciliation_cases.get_case(command.case_id)
            if case is None:
                raise ReconciliationCaseNotFound(case_id=str(command.case_id))
            if case.status is ReconciliationCaseStatus.RESOLVED:
                raise ReconciliationCaseAlreadyResolved(case_id=str(command.case_id))
            case.status = ReconciliationCaseStatus.RESOLVED
            case.resolved_at = _as_utc(command.occurred_at)
            case.resolved_by_user_id = actor.actor_id
            case.resolution = resolution
            case.resolution_notes = command.notes
            self._uow.reconciliation_cases.save_case(case)
            record_history(
                self._uow.task_history,
                pickup_task_id=case.resource_id,
                action="offline_reconciliation_resolved",
                actor=actor,
                previous_status=ReconciliationCaseStatus.OPEN.value,
                new_status=ReconciliationCaseStatus.RESOLVED.value,
                occurred_at=case.resolved_at,
                request_id=command.request_id,
                details={
                    "case_id": str(case.case_id),
                    "resolution": resolution.value,
                    "reason_code": case.reason_code.value,
                    "custody_implication": case.custody_implication,
                },
            )
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return case

    def list_cases(
        self, *, driver_user_id: str | None = None, status: str | None = None
    ) -> tuple[OfflineReconciliationCase, ...]:
        """Operations read of preserved offline conflicts."""
        return self._uow.reconciliation_cases.list_cases(
            driver_user_id=driver_user_id,
            status=status,
        )

    def list_authorizations(self, driver_user_id: str) -> tuple[OfflineAuthorization, ...]:
        return self._uow.offline_authorizations.list_for_driver(driver_user_id)

    # ------------------------------------------------------------------- helpers

    def _decode_claims(self, command: SyncOfflineWorkCommand) -> dict[str, Any]:
        try:
            claims = decode_offline_token(
                command.authorization_token,
                signing_key=self._signing_key,
            )
        except InvalidOfflineToken as exc:
            raise OfflineAuthorizationInvalid(str(exc)) from exc
        if claims.get("sub") != command.driver_user_id:
            raise OfflineAuthorizationInvalid("token is not bound to this driver")
        if claims.get("dev") != hash_device_id(command.device_id):
            raise OfflineAuthorizationInvalid("token is not bound to this device")
        return claims

    def _load_bound_authorization(
        self, command: SyncOfflineWorkCommand, claims: dict[str, Any]
    ) -> OfflineAuthorization:
        try:
            authorization_id = UUID(str(claims["jti"]))
        except (KeyError, ValueError) as exc:
            raise OfflineAuthorizationInvalid("token has no authorization id") from exc
        authorization = self._uow.offline_authorizations.get_authorization(authorization_id)
        if (
            authorization is None
            or authorization.driver_user_id != command.driver_user_id
            or authorization.device_id_hash != hash_device_id(command.device_id)
            or authorization.token_hash != hash_offline_token(command.authorization_token)
        ):
            raise OfflineAuthorizationNotFound()
        if authorization.is_revoked:
            raise OfflineAuthorizationRevoked(reason=authorization.revoked_reason)
        if _as_utc(command.occurred_at) > authorization.sync_deadline:
            raise OfflineSyncDeadlinePassed(
                sync_deadline=authorization.sync_deadline.isoformat()
            )
        return authorization

    def _load_or_create_stream(
        self, command: SyncOfflineWorkCommand, authorization: OfflineAuthorization
    ) -> OfflineStream:
        stream = self._uow.offline_streams.get_stream(command.stream_id)
        if stream is not None:
            if (
                stream.authorization_id != authorization.authorization_id
                or stream.driver_user_id != command.driver_user_id
                or stream.device_id_hash != authorization.device_id_hash
            ):
                raise OfflineAuthorizationNotFound()
            return stream
        stream = OfflineStream(
            stream_id=command.stream_id,
            authorization_id=authorization.authorization_id,
            driver_user_id=command.driver_user_id,
            device_id_hash=authorization.device_id_hash,
        )
        self._uow.offline_streams.save_stream(stream)
        return stream

    def _current_snapshot(self, resource_id: UUID) -> dict[str, Any]:
        task = self._uow.pickup_tasks.get_pickup_task(resource_id)
        if task is None:
            return {"resource_id": str(resource_id), "present": False}
        return _snapshot(task, current_assignment_revision(task))

    def _load_task(self, pickup_task_id: UUID) -> PickupTask:
        task = self._uow.pickup_tasks.get_pickup_task(pickup_task_id)
        if task is None:
            raise PickupTaskNotFound(str(pickup_task_id))
        return task


def _allowed_operations(actor: PickupActor) -> tuple[OfflineOperation, ...]:
    """Offline authority never exceeds what the actor may do online."""
    _ = actor
    return tuple(
        item for item in OfflineOperation if item is not OfflineOperation.ACCEPT_CUSTODY
    )


def _snapshot(task: PickupTask, revision: str) -> dict[str, Any]:
    return {
        "pickup_task_id": str(task.pickup_task_id),
        "shipment_id": str(task.shipment_id),
        "status": task.status.value,
        "assignment_state": task.assignment_state.value,
        "acceptance_state": task.acceptance_state.value if task.acceptance_state else None,
        "assigned_driver_user_id": task.assigned_driver_user_id,
        "assignment_revision": revision,
        "version": task.version,
        "terminal": task.is_terminal or task.is_accepted,
    }


def _submission_json(submission: OfflineEventSubmission) -> dict[str, Any]:
    return {
        "operation_id": str(submission.operation_id),
        "sequence": submission.sequence,
        "operation": _coerce_operation(submission.operation).value,
        "resource_type": submission.resource_type.value,
        "resource_id": str(submission.resource_id),
        "assignment_revision": submission.assignment_revision,
        "captured_at": _as_utc(submission.captured_at).isoformat(),
        "payload_fingerprint": submission.payload_fingerprint,
        "payload": dict(submission.payload),
    }


def _outcome(record: OfflineEventRecord, *, detail: str) -> SyncOutcome:
    return SyncOutcome(
        event_row_id=record.event_row_id,
        operation_id=record.operation_id,
        sequence=record.sequence,
        status=record.status,
        outcome_code=record.outcome_code,
        detail=detail,
        replayed=record.replayed,
    )


def _coerce_operation(value: OfflineOperation | str) -> OfflineOperation:
    if isinstance(value, OfflineOperation):
        return value
    try:
        return OfflineOperation(str(value).strip().upper())
    except ValueError as exc:
        raise OfflineOperationNotAuthorized(operations=(str(value),)) from exc


def _coerce_resolution(
    value: ReconciliationResolution | str,
) -> ReconciliationResolution:
    if isinstance(value, ReconciliationResolution):
        return value
    try:
        return ReconciliationResolution(str(value).strip().upper())
    except ValueError as exc:
        msg = f"unknown reconciliation resolution: {value}"
        raise OfflineAuthorizationInvalid(msg) from exc


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
