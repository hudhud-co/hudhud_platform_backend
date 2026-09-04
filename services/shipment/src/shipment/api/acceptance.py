"""Acceptance scan command HTTP adapter."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from shipment.api.dependencies import (
    get_acceptance_authorizer,
    get_acceptance_service,
    require_bearer_token,
    require_idempotency_key,
)
from shipment.api.errors import http_exception_for_domain_error
from shipment.api.schemas import AcceptanceScanRequest, AcceptanceScanResponse
from shipment.application.acceptance_service import (
    AcceptanceLifecycleService,
    RecordAcceptanceScanCommand,
)
from shipment.domain.errors import ShipmentError
from shipment.domain.value_objects import EvidenceReference
from shipment.ports.authorization import (
    AcceptanceAuthorizationOutcome,
    AcceptanceAuthorizer,
    AuthorizerUnavailableError,
)

router = APIRouter(prefix="/v1/shipments", tags=["acceptance"])


@router.post(
    "/{shipment_id}/acceptance-scans",
    response_model=AcceptanceScanResponse,
    status_code=200,
)
async def record_acceptance_scan(
    shipment_id: UUID,
    body: AcceptanceScanRequest,
    bearer_token: Annotated[str, Depends(require_bearer_token)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[AcceptanceLifecycleService, Depends(get_acceptance_service)],
    authorizer: Annotated[AcceptanceAuthorizer, Depends(get_acceptance_authorizer)],
) -> AcceptanceScanResponse:
    """Record an acceptance scan after authorization and DB commit."""
    try:
        decision = await authorizer.authorize_acceptance_scan(
            bearer_token=bearer_token,
            shipment_id=shipment_id,
            pickup_task_id=body.pickup_task_id,
        )
    except AuthorizerUnavailableError:
        raise HTTPException(status_code=503, detail="authorization unavailable") from None

    if decision.outcome is AcceptanceAuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail="authentication required")
    if decision.outcome is AcceptanceAuthorizationOutcome.FORBIDDEN or decision.actor is None:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        evidence = tuple(
            EvidenceReference.from_reference(
                item.storage_uri,
                captured_at=item.captured_at,
                location_label=item.location_label,
            )
            for item in body.exception_evidence
        )
    except ShipmentError as exc:
        raise http_exception_for_domain_error(exc) from None

    command = RecordAcceptanceScanCommand(
        shipment_id=shipment_id,
        pickup_task_id=body.pickup_task_id,
        acting_driver_user_id=decision.actor.user_id,
        scanned_identifier=body.scanned_identifier,
        scan_timestamp=body.scan_timestamp,
        outcome=body.outcome,
        idempotency_key=idempotency_key,
        exception_evidence=evidence,
    )
    try:
        result = await service.record_acceptance_scan(command)
    except ShipmentError as exc:
        raise http_exception_for_domain_error(exc) from None

    return AcceptanceScanResponse(
        shipment_id=result.shipment.shipment_id,
        pickup_task_id=result.pickup_task.pickup_task_id,
        current_status=result.shipment.current_status.value,
        acceptance_state=(
            result.pickup_task.acceptance_state.value
            if result.pickup_task.acceptance_state is not None
            else None
        ),
        outcome=body.outcome.value,
        accepted_at=result.shipment.accepted_at,
        sla_started_at=result.shipment.sla_started_at,
        current_custody_type=(
            result.shipment.current_custody_type.value
            if result.shipment.current_custody_type is not None
            else None
        ),
        current_custody_id=result.shipment.current_custody_id,
        shipment_event_id=(
            result.shipment_event.event_id if result.shipment_event is not None else None
        ),
        audit_id=result.audit_log.audit_id,
        idempotent_replay=result.idempotent_replay,
    )
