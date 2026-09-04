"""Pickup recovery command HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from pickup.api.dependencies import (
    get_recovery_authorizer,
    get_recovery_service,
    require_bearer_token,
    require_idempotency_key,
)
from pickup.api.errors import raise_http_for_domain_error
from pickup.api.schemas import (
    PickupTaskResponse,
    ReassignCommandRequest,
    RecoveryCommandRequest,
    RecoveryCommandResponse,
    RecoveryHistoryResponse,
    RescheduleCommandRequest,
)
from pickup.application.recovery_service import (
    PickupRecoveryService,
    ReassignRecoveryCommand,
    RecoveryCommand,
    RecoveryResult,
    RescheduleRecoveryCommand,
)
from pickup.domain.entities import PickupTask, RecoveryHistoryEntry
from pickup.domain.value_objects import RecoveryAction, ScheduledWindow
from pickup.ports.recovery_authorizer import (
    AuthorizerUnavailableError,
    RecoveryAuthorizationOutcome,
    RecoveryAuthorizer,
)

router = APIRouter(prefix="/pickup/tasks", tags=["pickup-recovery"])


def _task_response(task: PickupTask) -> PickupTaskResponse:
    return PickupTaskResponse(
        pickup_task_id=task.pickup_task_id,
        shipment_id=task.shipment_id,
        assigned_driver_user_id=task.assigned_driver_user_id,
        assigned_batch_id=task.assigned_batch_id,
        status=task.status.value,
        attempt_number=task.attempt_number,
        root_attempt_id=task.root_attempt_id,
        parent_attempt_id=task.parent_attempt_id,
        superseded_by_task_id=task.superseded_by_task_id,
        scheduled_window_start=task.scheduled_window_start,
        scheduled_window_end=task.scheduled_window_end,
        acceptance_state=task.acceptance_state.value if task.acceptance_state else None,
        recovery_reason=task.recovery_reason,
        created_at=task.created_at,
        recovered_at=task.recovered_at,
        cancelled_at=task.cancelled_at,
        version=task.version,
    )


def _history_response(entry: RecoveryHistoryEntry) -> RecoveryHistoryResponse:
    return RecoveryHistoryResponse(
        history_id=entry.history_id,
        pickup_task_id=entry.pickup_task_id,
        replacement_task_id=entry.replacement_task_id,
        action=entry.action.value,
        reason=entry.reason,
        idempotency_key=entry.idempotency_key,
        occurred_at=entry.occurred_at,
    )


def _result_response(result: RecoveryResult, action: RecoveryAction) -> RecoveryCommandResponse:
    return RecoveryCommandResponse(
        action=action.value,
        original_task=_task_response(result.original_task),
        replacement_task=(
            _task_response(result.replacement_task) if result.replacement_task is not None else None
        ),
        history_entry=_history_response(result.history_entry),
        idempotent_replay=result.idempotent_replay,
    )


async def _authorize(
    *,
    authorizer: RecoveryAuthorizer,
    bearer_token: str,
    pickup_task_id: UUID,
    action: RecoveryAction,
) -> None:
    try:
        decision = await authorizer.authorize_recovery(
            bearer_token=bearer_token,
            pickup_task_id=pickup_task_id,
            action=action,
        )
    except AuthorizerUnavailableError:
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if decision.outcome is RecoveryAuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail="authentication required")
    if decision.outcome is RecoveryAuthorizationOutcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("/{pickup_task_id}/retry", response_model=RecoveryCommandResponse)
async def retry_pickup(
    pickup_task_id: UUID,
    body: RecoveryCommandRequest,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[PickupRecoveryService, Depends(get_recovery_service)],
    authorizer: Annotated[RecoveryAuthorizer, Depends(get_recovery_authorizer)],
) -> RecoveryCommandResponse:
    await _authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        pickup_task_id=pickup_task_id,
        action=RecoveryAction.RETRY,
    )
    try:
        result = service.retry_pickup(
            RecoveryCommand(
                pickup_task_id=pickup_task_id,
                idempotency_key=idempotency_key,
                reason=body.reason,
                occurred_at=datetime.now(tz=UTC),
                expected_version=body.expected_version,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _result_response(result, RecoveryAction.RETRY)


@router.post("/{pickup_task_id}/reschedule", response_model=RecoveryCommandResponse)
async def reschedule_pickup(
    pickup_task_id: UUID,
    body: RescheduleCommandRequest,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[PickupRecoveryService, Depends(get_recovery_service)],
    authorizer: Annotated[RecoveryAuthorizer, Depends(get_recovery_authorizer)],
) -> RecoveryCommandResponse:
    await _authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        pickup_task_id=pickup_task_id,
        action=RecoveryAction.RESCHEDULE,
    )
    try:
        result = service.reschedule_pickup(
            RescheduleRecoveryCommand(
                pickup_task_id=pickup_task_id,
                idempotency_key=idempotency_key,
                reason=body.reason,
                occurred_at=datetime.now(tz=UTC),
                expected_version=body.expected_version,
                scheduled_window=ScheduledWindow(
                    start=body.scheduled_window.start,
                    end=body.scheduled_window.end,
                ),
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _result_response(result, RecoveryAction.RESCHEDULE)


@router.post("/{pickup_task_id}/reassign", response_model=RecoveryCommandResponse)
async def reassign_pickup(
    pickup_task_id: UUID,
    body: ReassignCommandRequest,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[PickupRecoveryService, Depends(get_recovery_service)],
    authorizer: Annotated[RecoveryAuthorizer, Depends(get_recovery_authorizer)],
) -> RecoveryCommandResponse:
    await _authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        pickup_task_id=pickup_task_id,
        action=RecoveryAction.REASSIGN,
    )
    try:
        result = service.reassign_pickup(
            ReassignRecoveryCommand(
                pickup_task_id=pickup_task_id,
                idempotency_key=idempotency_key,
                reason=body.reason,
                occurred_at=datetime.now(tz=UTC),
                expected_version=body.expected_version,
                new_driver_user_id=body.new_driver_user_id,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _result_response(result, RecoveryAction.REASSIGN)


@router.post("/{pickup_task_id}/cancel", response_model=RecoveryCommandResponse)
async def cancel_pickup(
    pickup_task_id: UUID,
    body: RecoveryCommandRequest,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[PickupRecoveryService, Depends(get_recovery_service)],
    authorizer: Annotated[RecoveryAuthorizer, Depends(get_recovery_authorizer)],
) -> RecoveryCommandResponse:
    await _authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        pickup_task_id=pickup_task_id,
        action=RecoveryAction.CANCEL,
    )
    try:
        result = service.cancel_pickup(
            RecoveryCommand(
                pickup_task_id=pickup_task_id,
                idempotency_key=idempotency_key,
                reason=body.reason,
                occurred_at=datetime.now(tz=UTC),
                expected_version=body.expected_version,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _result_response(result, RecoveryAction.CANCEL)
