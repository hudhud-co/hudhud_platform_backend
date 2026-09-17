"""Sender handover ceremony and driver-to-hub handover HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pickup.api.driver_dependencies import (
    Authorizer,
    BearerToken,
    authorize,
    get_hub_handover_service,
    get_verification_service,
)
from pickup.api.driver_schemas import (
    CancelHandoverManifestRequest,
    CloseHandoverManifestRequest,
    ConfirmCourierManifestRequest,
    CourierManifestResponse,
    CreateHandoverManifestRequest,
    HandoverManifestItemResponse,
    HandoverManifestResponse,
    HubReceiptResponse,
    IssuedChallengeResponse,
    RecordHubReceiptRequest,
    SubmitCourierManifestRequest,
    VerificationResponse,
    VerifyChallengeRequest,
)
from pickup.api.errors import raise_http_for_domain_error
from pickup.application.handover_verification_service import (
    ConfirmManifestCommand,
    CourierHandoverVerificationService,
    IssueChallengeCommand,
    ManifestResult,
    SubmitManifestCommand,
    VerifyChallengeCommand,
)
from pickup.application.hub_handover_service import (
    CloseManifestCommand,
    CreateHandoverManifestCommand,
    HandoverManifestView,
    HubHandoverService,
    HubReceiptResult,
    ManifestLifecycleCommand,
    RecordHubReceiptCommand,
)
from pickup.domain.errors import HubScopeNotAuthorized
from pickup.domain.handover import HandoverDiscrepancyReason, HandoverManifestItem
from pickup.domain.value_objects import EvidenceMediaRef
from pickup.ports.authorization import PickupCommand, PickupRole

router = APIRouter(prefix="/pickup", tags=["pickup-handover"])

Verification = Annotated[
    CourierHandoverVerificationService, Depends(get_verification_service)
]
HubHandover = Annotated[HubHandoverService, Depends(get_hub_handover_service)]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _manifest_result(result: ManifestResult) -> CourierManifestResponse:
    manifest = result.manifest
    return CourierManifestResponse(
        manifest_id=manifest.manifest_id,
        pickup_task_id=manifest.pickup_task_id,
        shipment_id=manifest.shipment_id,
        manifest_digest=manifest.manifest_digest,
        status=manifest.status.value,
        submitted_at=manifest.submitted_at,
        confirmed_at=manifest.confirmed_at,
        confirmation_valid_until=manifest.confirmation_valid_until,
        idempotent_replay=result.replayed,
    )


def _item_response(item: HandoverManifestItem) -> HandoverManifestItemResponse:
    return HandoverManifestItemResponse(
        item_id=item.item_id,
        pickup_task_id=item.pickup_task_id,
        shipment_id=item.shipment_id,
        status=item.status.value,
        added_at=item.added_at,
        received_at=item.received_at,
        received_hub_id=item.received_hub_id,
        discrepancy_reason=(
            item.discrepancy_reason.value if item.discrepancy_reason else None
        ),
        releases_custody=item.releases_custody,
    )


def _handover_response(
    view: HandoverManifestView | HubReceiptResult,
    *,
    items: tuple[HandoverManifestItem, ...] | None = None,
) -> HandoverManifestResponse:
    manifest = view.manifest
    resolved_items = items if items is not None else getattr(view, "items", ())
    return HandoverManifestResponse(
        manifest_id=manifest.manifest_id,
        manifest_code=manifest.manifest_code,
        driver_user_id=manifest.driver_user_id,
        hub_id=manifest.hub_id,
        status=manifest.status.value,
        expected_count=manifest.expected_count,
        received_count=manifest.received_count,
        missing_count=manifest.missing_count,
        discrepancy_count=manifest.discrepancy_count,
        created_at=manifest.created_at,
        arrived_at_hub_at=manifest.arrived_at_hub_at,
        completed_at=manifest.completed_at,
        cancelled_at=manifest.cancelled_at,
        items=[_item_response(item) for item in resolved_items],
        version=manifest.version,
        idempotent_replay=getattr(view, "replayed", False),
    )


# ------------------------------------------------------------ sender ceremony


@router.post(
    "/tasks/{pickup_task_id}/courier-challenge",
    response_model=IssuedChallengeResponse,
    status_code=201,
)
async def issue_courier_challenge(
    pickup_task_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Verification,
) -> IssuedChallengeResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.CHALLENGE_ISSUE,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        issued = service.issue_challenge(
            IssueChallengeCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return IssuedChallengeResponse(
        challenge_id=issued.challenge_id,
        pickup_task_id=issued.pickup_task_id,
        challenge_payload=issued.payload,
        expires_at=issued.expires_at,
        ttl_seconds=issued.ttl_seconds,
        status=issued.status.value,
        reissued=issued.reissued,
    )


@router.post(
    "/tasks/{pickup_task_id}/courier-challenge/verify",
    response_model=VerificationResponse,
)
async def verify_courier_challenge(
    pickup_task_id: UUID,
    body: VerifyChallengeRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Verification,
) -> VerificationResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.CHALLENGE_VERIFY,
        resource_id=pickup_task_id,
    )
    try:
        result = service.verify_challenge(
            VerifyChallengeCommand(
                pickup_task_id=pickup_task_id,
                presented_payload=body.presented_payload,
                challenge_id=body.challenge_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return VerificationResponse(
        challenge_id=result.challenge_id,
        pickup_task_id=result.pickup_task_id,
        status=result.status.value,
        courier_user_id=result.courier_user_id,
        verified_at=result.verified_at,
        valid_until=result.valid_until,
        idempotent_replay=result.replayed,
    )


@router.post(
    "/tasks/{pickup_task_id}/courier-manifest",
    response_model=CourierManifestResponse,
    status_code=201,
)
async def submit_courier_manifest(
    pickup_task_id: UUID,
    body: SubmitCourierManifestRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Verification,
) -> CourierManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.MANIFEST_SUBMIT,
        resource_id=pickup_task_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        result = service.submit_manifest(
            SubmitManifestCommand(
                pickup_task_id=pickup_task_id,
                acting_driver_user_id=actor.actor_id,
                scanned_identifier=body.scanned_identifier,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _manifest_result(result)


@router.post(
    "/tasks/{pickup_task_id}/courier-manifest/confirm",
    response_model=CourierManifestResponse,
)
async def confirm_courier_manifest(
    pickup_task_id: UUID,
    body: ConfirmCourierManifestRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: Verification,
) -> CourierManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.MANIFEST_CONFIRM,
        resource_id=pickup_task_id,
    )
    try:
        result = service.confirm_manifest(
            ConfirmManifestCommand(
                pickup_task_id=pickup_task_id,
                occurred_at=_now(),
                expected_manifest_digest=body.expected_manifest_digest,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _manifest_result(result)


# --------------------------------------------------------------- hub handover


@router.post(
    "/handover-manifests", response_model=HandoverManifestResponse, status_code=201
)
async def create_handover_manifest(
    body: CreateHandoverManifestRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HandoverManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_CREATE,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        view = service.create_manifest(
            CreateHandoverManifestCommand(
                driver_user_id=actor.actor_id,
                hub_id=body.hub_id,
                pickup_task_ids=tuple(body.pickup_task_ids),
                occurred_at=_now(),
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _handover_response(view)


@router.post(
    "/handover-manifests/{manifest_id}/arrive", response_model=HandoverManifestResponse
)
async def arrive_at_hub(
    manifest_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HandoverManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_ARRIVE,
        resource_id=manifest_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        view = service.arrive_at_hub(
            ManifestLifecycleCommand(
                manifest_id=manifest_id,
                driver_user_id=actor.actor_id,
                occurred_at=_now(),
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _handover_response(view)


@router.post(
    "/handover-manifests/{manifest_id}/cancel", response_model=HandoverManifestResponse
)
async def cancel_handover_manifest(
    manifest_id: UUID,
    body: CancelHandoverManifestRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HandoverManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_CANCEL,
        resource_id=manifest_id,
        require_role=PickupRole.PICKUP_DRIVER,
    )
    try:
        view = service.cancel_manifest(
            ManifestLifecycleCommand(
                manifest_id=manifest_id,
                driver_user_id=actor.actor_id,
                occurred_at=_now(),
                reason=body.reason,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _handover_response(view)


@router.post(
    "/handover-manifests/{manifest_id}/receipts", response_model=HubReceiptResponse
)
async def record_hub_receipt(
    manifest_id: UUID,
    body: RecordHubReceiptRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HubReceiptResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_RECEIVE,
        resource_id=manifest_id,
        require_role=PickupRole.HUB_OPERATOR,
    )
    if not actor.scoped_to_hub(body.receiving_hub_id):
        raise_http_for_domain_error(
            HubScopeNotAuthorized(hub_id=str(body.receiving_hub_id))
        )
    try:
        result = service.record_hub_receipt(
            RecordHubReceiptCommand(
                manifest_id=manifest_id,
                shipment_id=body.shipment_id,
                receiving_hub_id=body.receiving_hub_id,
                occurred_at=_now(),
                discrepancy_reason=(
                    HandoverDiscrepancyReason(body.discrepancy_reason)
                    if body.discrepancy_reason
                    else None
                ),
                notes=body.notes,
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
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return HubReceiptResponse(
        manifest=_handover_response(
            result, items=service.list_items(manifest_id)
        ),
        item=_item_response(result.item),
        custody_released=result.custody_released,
        event_id=(
            result.outbox_record.event_id if result.outbox_record is not None else None
        ),
        idempotent_replay=result.replayed,
    )


@router.post(
    "/handover-manifests/{manifest_id}/close", response_model=HandoverManifestResponse
)
async def close_handover_manifest(
    manifest_id: UUID,
    body: CloseHandoverManifestRequest,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HandoverManifestResponse:
    actor = await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_CLOSE,
        resource_id=manifest_id,
        require_role=PickupRole.HUB_OPERATOR,
    )
    try:
        view = service.close_manifest(
            CloseManifestCommand(
                manifest_id=manifest_id,
                occurred_at=_now(),
                missing_shipment_ids=tuple(body.missing_shipment_ids),
                notes=body.notes,
            ),
            actor=actor,
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _handover_response(view)


@router.get(
    "/handover-manifests/{manifest_id}", response_model=HandoverManifestResponse
)
async def read_handover_manifest(
    manifest_id: UUID,
    bearer_token: BearerToken,
    authorizer: Authorizer,
    service: HubHandover,
) -> HandoverManifestResponse:
    await authorize(
        authorizer=authorizer,
        bearer_token=bearer_token,
        command=PickupCommand.HANDOVER_READ,
        resource_id=manifest_id,
    )
    try:
        view = service.read_manifest(manifest_id)
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _handover_response(view)
