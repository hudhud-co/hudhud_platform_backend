"""Offline work authorization, synchronization, and reconciliation HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pickup.api.driver_dependencies import (
    Authorizer,
    BearerToken,
    authorize,
    get_offline_sync_service,
)
from pickup.api.driver_schemas import (
    IssuedOfflineAuthorizationResponse,
    IssueOfflineAuthorizationRequest,
    OfflineAuthorizationResponse,
    ReconciliationCaseListResponse,
    ReconciliationCaseResponse,
    ResolveReconciliationRequest,
    RevokeOfflineAuthorizationRequest,
    SyncOfflineWorkRequest,
    SyncOfflineWorkResponse,
    SyncOutcomeResponse,
)
from pickup.api.errors import raise_http_for_domain_error
from pickup.application.offline_sync_service import (
    IssueOfflineAuthorizationCommand,
    OfflineEventSubmission,
    OfflineSyncService,
    ResolveReconciliationCommand,
    RevokeOfflineAuthorizationCommand,
    SyncOfflineWorkCommand,
)
from pickup.domain.errors import OfflineOperationNotAuthorized
from pickup.domain.offline import (
    OfflineAuthorization,
    OfflineOperation,
    OfflineReconciliationCase,
    OfflineResourceType,
)
from pickup.ports.authorization import PickupCommand, PickupRole

router = APIRouter(prefix="/pickup/offline", tags=["pickup-offline"])

Offline = Annotated[OfflineSyncService, Depends(get_offline_sync_service)]

#: Client obligations that make an offline capture auditable and replay-safe.
CLIENT_REQUIREMENTS = {
    "encrypted_at_rest": True,
    "append_only_event_log": True,
    "strict_sequence": True,
    "no_undownloaded_work": True,
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _authorization_response(
    authorization: OfflineAuthorization,
) -> OfflineAuthorizationResponse:
    return OfflineAuthorizationResponse(
        authorization_id=authorization.authorization_id,
        resource_type=authorization.resource_type.value,
        resource_id=authorization.resource_id,
        assignment_revision=authorization.assignment_revision,
        permitted_operations=[
            item.value for item in authorization.permitted_operations
        ],
        issued_at=authorization.issued_at,
        expires_at=authorization.expires_at,
        sync_deadline=authorization.sync_deadline,
        resource_snapshot=dict(authorization.resource_snapshot),
        revoked_at=authorization.revoked_at,
        revoked_reason=authorization.revoked_reason,
    )


def _case_response(case: OfflineReconciliationCase) -> ReconciliationCaseResponse:
    return ReconciliationCaseResponse(
        case_id=case.case_id,
        offline_event_id=case.offline_event_row_id,
        driver_user_id=case.driver_user_id,
        resource_type=case.resource_type.value,
        resource_id=case.resource_id,
        status=case.status.value,
        reason_code=case.reason_code.value,
        reason_detail=case.reason_detail,
        authoritative_state=dict(case.authoritative_state),
        submitted_event=dict(case.submitted_event),
        custody_implication=case.custody_implication,
        opened_at=case.opened_at,
        resolved_at=case.resolved_at,
        resolved_by_user_id=case.resolved_by_user_id,
        resolution=case.resolution.value if case.resolution else None,
        resolution_notes=case.resolution_notes,
    )


def _coerce_operations(values: list[str]) -> tuple[OfflineOperation, ...]:
    operations: list[OfflineOperation] = []
    unknown: list[str] = []
    for value in values:
        try:
            operations.append(OfflineOperation(value.strip().upper()))
        except ValueError:
            unknown.append(value)
    if unknown:
        raise OfflineOperationNotAuthorized(operations=tuple(sorted(unknown)))
    return tuple(operations)


@router.post(
    "/authorizations",
    response_model=IssuedOfflineAuthorizationResponse,
    status_code=201,
)
async def issue_offline_authorization(
    body: IssueOfflineAuthorizationRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Offline,
) -> IssuedOfflineAuthorizationResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.OFFLINE_AUTHORIZE,
        resource_id=body.pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        issued = service.issue_authorization(
            IssueOfflineAuthorizationCommand(
                driver_user_id=actor.actor_id,
                device_id=body.device_id,
                resource_id=body.pickup_task_id,
                occurred_at=_now(),
                requested_operations=_coerce_operations(body.requested_operations),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return IssuedOfflineAuthorizationResponse(
        authorization=_authorization_response(issued.authorization),
        authorization_token=issued.token,
        client_requirements=dict(CLIENT_REQUIREMENTS),
    )


@router.post(
    "/authorizations/{authorization_id}/revoke",
    response_model=OfflineAuthorizationResponse,
)
async def revoke_offline_authorization(
    authorization_id: UUID,
    body: RevokeOfflineAuthorizationRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Offline,
) -> OfflineAuthorizationResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.OFFLINE_REVOKE,
        resource_id=authorization_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        authorization = service.revoke_authorization(
            RevokeOfflineAuthorizationCommand(
                authorization_id=authorization_id,
                driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _authorization_response(authorization)


@router.post("/sync", response_model=SyncOfflineWorkResponse)
async def sync_offline_work(
    body: SyncOfflineWorkRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Offline,
) -> SyncOfflineWorkResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.OFFLINE_SYNC,
        resource_id=body.stream_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        submissions = tuple(
            OfflineEventSubmission(
                operation_id=event.operation_id,
                sequence=event.sequence,
                operation=event.operation,
                resource_id=event.resource_id,
                assignment_revision=event.assignment_revision,
                captured_at=event.captured_at,
                payload_fingerprint=event.payload_fingerprint,
                resource_type=OfflineResourceType.PICKUP_TASK,
                payload=dict(event.payload),
            )
            for event in body.events
        )
        result = service.sync(
            SyncOfflineWorkCommand(
                driver_user_id=actor.actor_id,
                device_id=body.device_id,
                stream_id=body.stream_id,
                authorization_token=body.authorization_token,
                events=submissions,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return SyncOfflineWorkResponse(
        stream_id=result.stream_id,
        authorization_id=result.authorization_id,
        last_contiguous_sequence=result.last_contiguous_sequence,
        outcomes=[
            SyncOutcomeResponse(
                event_id=outcome.event_row_id,
                operation_id=outcome.operation_id,
                sequence=outcome.sequence,
                status=outcome.status.value,
                outcome_code=outcome.outcome_code.value,
                detail=outcome.detail,
                replayed=outcome.replayed,
            )
            for outcome in result.outcomes
        ],
        counts=result.counts,
    )


@router.get(
    "/reconciliation-cases", response_model=ReconciliationCaseListResponse
)
async def list_reconciliation_cases(
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Offline,
    status: Annotated[str | None, Query(max_length=32)] = None,
    driver_user_id: Annotated[str | None, Query(max_length=128)] = None,
) -> ReconciliationCaseListResponse:
    await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.RECONCILIATION_READ,
        require_role=PickupRole.OPERATIONS,
    )
    try:
        cases = service.list_cases(driver_user_id=driver_user_id, status=status)
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return ReconciliationCaseListResponse(
        cases=[_case_response(case) for case in cases]
    )


@router.post(
    "/reconciliation-cases/{case_id}/resolve",
    response_model=ReconciliationCaseResponse,
)
async def resolve_reconciliation_case(
    case_id: UUID,
    body: ResolveReconciliationRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Offline,
) -> ReconciliationCaseResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.RECONCILIATION_RESOLVE,
        resource_id=case_id,
        require_role=PickupRole.OPERATIONS,
    )
    try:
        case = service.resolve_case(
            ResolveReconciliationCommand(
                case_id=case_id,
                resolution=body.resolution,
                occurred_at=_now(),
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _case_response(case)
