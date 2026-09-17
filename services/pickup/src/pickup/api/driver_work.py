"""Driver workforce and pickup task lifecycle HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pickup.api.driver_dependencies import (
    Authorizer,
    BearerToken,
    IdempotencyKey,
    authorize,
    get_acceptance_service,
    get_stop_service,
    get_task_lifecycle_service,
    get_work_session_service,
)
from pickup.api.driver_schemas import (
    AcceptanceStatusResponse,
    AcceptCustodyRequest,
    AcceptCustodyResponse,
    CaptureProofRequest,
    DeclineAssignmentRequest,
    EndWorkSessionRequest,
    FailTaskRequest,
    PauseWorkSessionRequest,
    RefusePickupRequest,
    ReportExceptionRequest,
    ResolveScanRequest,
    ScanResolutionResponse,
    ScanTaskRequest,
    StartWorkSessionRequest,
    StopReadinessResponse,
    TaskHistoryEntryResponse,
    TaskHistoryResponse,
    TaskLifecycleResponse,
    WorkloadResponse,
    WorkSessionResponse,
)
from pickup.api.errors import raise_http_for_domain_error
from pickup.application.acceptance_service import (
    AcceptPickupTaskCommand,
    PickupAcceptanceService,
)
from pickup.application.driver_session_service import (
    DriverWorkSessionService,
    EndWorkSessionCommand,
    PauseWorkSessionCommand,
    ResumeWorkSessionCommand,
    StartWorkSessionCommand,
    WorkSessionResult,
)
from pickup.application.stop_service import (
    PickupStopService,
    ResolveScanCommand,
)
from pickup.application.task_lifecycle_service import (
    CaptureProofCommand as ProofCommand,
)
from pickup.application.task_lifecycle_service import (
    DeclineAssignmentCommand,
    FailTaskCommand,
    PickupTaskLifecycleService,
    RefusePickupCommand,
    ReportExceptionCommand,
    ScanTaskCommand,
    TaskCommand,
    TaskLifecycleResult,
)
from pickup.domain.value_objects import EvidenceMediaRef
from pickup.ports.authorization import PickupCommand, PickupRole

router = APIRouter(prefix="/pickup", tags=["pickup-driver"])

WorkSessions = Annotated[DriverWorkSessionService, Depends(get_work_session_service)]
Tasks = Annotated[PickupTaskLifecycleService, Depends(get_task_lifecycle_service)]
Acceptance = Annotated[PickupAcceptanceService, Depends(get_acceptance_service)]
Stops = Annotated[PickupStopService, Depends(get_stop_service)]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _session_response(result: WorkSessionResult) -> WorkSessionResponse:
    session = result.session
    workload = result.workload
    return WorkSessionResponse(
        session_id=session.session_id,
        driver_user_id=session.driver_user_id,
        capability=session.capability.value,
        status=session.status.value,
        availability=session.availability.value,
        started_at=session.started_at,
        home_hub_id=session.home_hub_id,
        paused_at=session.paused_at,
        resumed_at=session.resumed_at,
        ended_at=session.ended_at,
        pause_reason=session.pause_reason.value if session.pause_reason else None,
        end_reason=session.end_reason.value if session.end_reason else None,
        version=session.version,
        workload=WorkloadResponse(
            open_custody_shipment_ids=list(workload.open_custody_shipment_ids),
            active_handover_manifest_ids=list(workload.active_handover_manifest_ids),
            active_task_ids=list(workload.active_task_ids),
            unsynced_offline_operation_ids=list(workload.unsynced_offline_operation_ids),
            end_blockers=list(workload.end_blockers()),
            pause_blockers=list(workload.pause_blockers()),
        ),
        idempotent_replay=result.replayed,
    )


def _task_response(result: TaskLifecycleResult) -> TaskLifecycleResponse:
    task = result.task
    return TaskLifecycleResponse(
        pickup_task_id=task.pickup_task_id,
        shipment_id=task.shipment_id,
        status=task.status.value,
        assignment_state=task.assignment_state.value,
        acceptance_state=task.acceptance_state.value if task.acceptance_state else None,
        assignment_revision=result.assignment_revision,
        has_pickup_condition_proof=task.has_pickup_condition_proof,
        version=task.version,
        idempotent_replay=result.replayed,
        packaging_assessment=(
            task.packaging_assessment.value if task.packaging_assessment else None
        ),
        condition_decision=(
            task.condition_decision.value if task.condition_decision else None
        ),
        stop_outcome=task.stop_outcome.value if task.stop_outcome else None,
        stop_outcome_reason=(
            task.stop_outcome_reason.value if task.stop_outcome_reason else None
        ),
    )


# --------------------------------------------------------------- work sessions


@router.post("/work-sessions/start", response_model=WorkSessionResponse)
async def start_work_session(
    body: StartWorkSessionRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: WorkSessions,
) -> WorkSessionResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.WORK_SESSION_START,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.start(
            StartWorkSessionCommand(
                driver_user_id=actor.actor_id,
                occurred_at=_now(),
                home_hub_id=body.home_hub_id,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _session_response(result)


@router.post("/work-sessions/{session_id}/pause", response_model=WorkSessionResponse)
async def pause_work_session(
    session_id: UUID,
    body: PauseWorkSessionRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: WorkSessions,
) -> WorkSessionResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.WORK_SESSION_PAUSE,
        resource_id=session_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.pause(
            PauseWorkSessionCommand(
                driver_user_id=actor.actor_id,
                session_id=session_id,
                reason=body.reason,
                notes=body.notes,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _session_response(result)


@router.post("/work-sessions/{session_id}/resume", response_model=WorkSessionResponse)
async def resume_work_session(
    session_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: WorkSessions,
) -> WorkSessionResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.WORK_SESSION_RESUME,
        resource_id=session_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.resume(
            ResumeWorkSessionCommand(
                driver_user_id=actor.actor_id,
                session_id=session_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _session_response(result)


@router.post("/work-sessions/{session_id}/end", response_model=WorkSessionResponse)
async def end_work_session(
    session_id: UUID,
    body: EndWorkSessionRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: WorkSessions,
) -> WorkSessionResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.WORK_SESSION_END,
        resource_id=session_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.end(
            EndWorkSessionCommand(
                driver_user_id=actor.actor_id,
                session_id=session_id,
                reason=body.reason,
                notes=body.notes,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _session_response(result)


@router.get("/work-sessions/current", response_model=WorkSessionResponse)
async def read_current_work_session(
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: WorkSessions,
) -> WorkSessionResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.WORK_SESSION_READ,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.get_status(actor.actor_id)
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _session_response(result)


# -------------------------------------------------------------- task lifecycle


@router.post("/tasks/{pickup_task_id}/acknowledge", response_model=TaskLifecycleResponse)
async def acknowledge_assignment(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_ACKNOWLEDGE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.acknowledge(
            TaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/decline", response_model=TaskLifecycleResponse)
async def decline_assignment(
    pickup_task_id: UUID,
    body: DeclineAssignmentRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_DECLINE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.decline(
            DeclineAssignmentCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/arrive", response_model=TaskLifecycleResponse)
async def arrive_at_pickup(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_ARRIVE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.arrive(
            TaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/scan", response_model=TaskLifecycleResponse)
async def scan_pickup(
    pickup_task_id: UUID,
    body: ScanTaskRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_SCAN,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.scan(
            ScanTaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                scanned_identifier=body.scanned_identifier,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/condition-proof", response_model=TaskLifecycleResponse)
async def capture_condition_proof(
    pickup_task_id: UUID,
    body: CaptureProofRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_CAPTURE_PROOF,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.capture_proof(
            ProofCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                package_condition_status=body.package_condition_status,
                condition_notes=body.condition_notes,
                evidence_present=body.evidence_present,
                packaging_assessment=body.packaging_assessment,
                decision=body.decision,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/refuse", response_model=TaskLifecycleResponse)
async def refuse_pickup(
    pickup_task_id: UUID,
    body: RefusePickupRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    """Refuse one parcel at the door. It stays with the merchant; no custody begins."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_REFUSE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.refuse(
            RefusePickupCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/not-presented", response_model=TaskLifecycleResponse)
async def mark_not_presented(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    """The merchant did not hand this parcel over — expected, unpicked, no penalty."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_NOT_PRESENTED,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.mark_not_presented(
            TaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.get("/tasks/{pickup_task_id}/acceptance", response_model=AcceptanceStatusResponse)
async def read_acceptance_status(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Stops,
) -> AcceptanceStatusResponse:
    """Answer "was my acceptance recorded?" without risking a second acceptance."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_READ_ACCEPTANCE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        status = service.acceptance_status(pickup_task_id=pickup_task_id, actor=actor)
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return AcceptanceStatusResponse(
        pickup_task_id=status.pickup_task_id,
        recorded=status.recorded,
        safe_to_retry=status.safe_to_retry,
        acceptance_state=(
            status.acceptance_state.value if status.acceptance_state else None
        ),
        accepted_at=status.accepted_at,  # type: ignore[arg-type]
        accepted_by_driver_user_id=status.accepted_by_driver_user_id,
    )


@router.post(
    "/batches/{assigned_batch_id}/scan-resolution",
    response_model=ScanResolutionResponse,
)
async def resolve_stop_scan(
    assigned_batch_id: UUID,
    body: ResolveScanRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Stops,
) -> ScanResolutionResponse:
    """Classify a scanned label against this stop. Read-only — it creates nothing."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.STOP_RESOLVE_SCAN,
        resource_id=assigned_batch_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        resolution = service.resolve_scan(
            ResolveScanCommand(
                assigned_batch_id=assigned_batch_id,
                scanned_identifier=body.scanned_identifier,
                unreadable=body.unreadable,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return ScanResolutionResponse(
        outcome=resolution.outcome.value,
        can_proceed=resolution.can_proceed,
        pickup_task_id=resolution.pickup_task_id,
        shipment_id=resolution.shipment_id,
    )


@router.get("/batches/{assigned_batch_id}/stop", response_model=StopReadinessResponse)
async def read_stop_readiness(
    assigned_batch_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Stops,
) -> StopReadinessResponse:
    """Outcome counts for the stop, and whether it may be completed."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.STOP_READ,
        resource_id=assigned_batch_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        readiness = service.stop_readiness(
            assigned_batch_id=assigned_batch_id, actor=actor
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return StopReadinessResponse(
        assigned_batch_id=readiness.assigned_batch_id,
        expected=readiness.expected,
        accepted=readiness.accepted,
        refused=readiness.refused,
        not_presented=readiness.not_presented,
        closed_by_recovery=readiness.closed_by_recovery,
        unresolved=readiness.unresolved,
        can_complete=readiness.can_complete,
        is_partial=readiness.is_partial,
        blocking_reason=readiness.blocking_reason,
    )


@router.post("/tasks/{pickup_task_id}/exception", response_model=TaskLifecycleResponse)
async def report_pickup_exception(
    pickup_task_id: UUID,
    body: ReportExceptionRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_REPORT_EXCEPTION,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.report_exception(
            ReportExceptionCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
                notes=body.notes,
                contact_attempted=body.contact_attempted,
                contact_attempt_count=body.contact_attempt_count,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/fail", response_model=TaskLifecycleResponse)
async def fail_pickup(
    pickup_task_id: UUID,
    body: FailTaskRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskLifecycleResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_FAIL,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.fail(
            FailTaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _task_response(result)


@router.post("/tasks/{pickup_task_id}/accept", response_model=AcceptCustodyResponse)
async def accept_custody(
    pickup_task_id: UUID,
    body: AcceptCustodyRequest,
    bearer_token: BearerToken,
    idempotency_key: IdempotencyKey,
    authorizer: Authorizer,
    service: Acceptance,
) -> AcceptCustodyResponse:
    """Start pickup-driver custody and enqueue pickup.fact.accepted transactionally."""
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_ACCEPT,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.accept_pickup_task(
            AcceptPickupTaskCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                scanned_identifier=body.scanned_identifier,
                outcome=body.outcome,
                idempotency_key=idempotency_key,
                accepted_at=_now(),
                media_refs=tuple(
                    EvidenceMediaRef(
                        ref_type=ref.ref_type,
                        bucket=ref.bucket,
                        key=ref.key,
                        content_type=ref.content_type,
                    )
                    for ref in body.media_refs
                ),
                correlation_id=body.correlation_id,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    task = result.pickup_task
    return AcceptCustodyResponse(
        pickup_task_id=task.pickup_task_id,
        shipment_id=task.shipment_id,
        acceptance_state=task.acceptance_state.value if task.acceptance_state else "",
        accepted_at=task.accepted_at,
        accepted_by_driver_user_id=task.accepted_by_driver_user_id,
        event_id=result.event_id,
        aggregate_version=result.aggregate_version,
        idempotent_replay=result.idempotent_replay,
    )


@router.get("/tasks/{pickup_task_id}/history", response_model=TaskHistoryResponse)
async def read_task_history(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Tasks,
) -> TaskHistoryResponse:
    await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.TASK_READ,
        resource_id=pickup_task_id,
    )
    try:
        entries = service.list_history(pickup_task_id)
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return TaskHistoryResponse(
        pickup_task_id=pickup_task_id,
        entries=[
            TaskHistoryEntryResponse(
                history_id=entry.history_id,
                pickup_task_id=entry.pickup_task_id,
                action=entry.action,
                actor_id=entry.actor_id,
                actor_role=entry.actor_role,
                previous_status=entry.previous_status,
                new_status=entry.new_status,
                occurred_at=entry.occurred_at,
                request_id=entry.request_id,
                details=dict(entry.details),
            )
            for entry in entries
        ],
    )
