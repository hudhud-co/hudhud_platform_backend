"""Hub HTTP adapter.

The recurring check here is *posting*: a hub operator is posted to particular facilities,
and being staff at one hub is not authority at another. Operations sees everything.

Every custody-changing action takes its actor from the authorization decision, never from
the body (SEC-09), which is also what makes the CUS-05 rule enforceable: the service can
tell whether the caller really is hub staff.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from hub.api.errors import raise_http_for_domain_error
from hub.api.schemas import (
    AcceptDropOffRequest,
    ActivityResponse,
    AddParcelRequest,
    ApplyLabelRequest,
    CaptureDetailsRequest,
    ConsignmentResponse,
    DeviationRequest,
    DispatchRequest,
    DropOffResponse,
    ExpectDropOffRequest,
    HandoverRequest,
    HoldRequest,
    HoldResponse,
    HubResponse,
    LinehaulResponse,
    OpenConsignmentRequest,
    PlanLinehaulRequest,
    PositionRequest,
    PositionResponse,
    PresenceResponse,
    RegisterHubRequest,
    ScanInRequest,
    SealCheckRequest,
    SealCheckResponse,
    SealRequest,
    SetCutOffRequest,
    SortRequest,
)
from hub.application.drop_off_service import DropOffService
from hub.application.hub_admin_service import HubAdminService
from hub.application.linehaul_service import LinehaulService, SealCheckResult
from hub.application.processing_service import HoldOutcome, HubProcessingService
from hub.domain.entities import (
    Consignment,
    DropOff,
    Hub,
    HubActivitySnapshot,
    Linehaul,
    ParcelPresence,
    VehiclePosition,
)
from hub.domain.errors import HubError
from hub.domain.value_objects import (
    GeoPoint,
    HoldReason,
    PositionSource,
    Urgency,
)
from hub.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    HubActor,
    HubAuthorizer,
    HubCommand,
)

router = APIRouter(prefix="/hub", tags=["hub"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_unit_of_work(request: Request):
    """One unit of work per request, built here and shared with nothing else.

    FastAPI caches a dependency's value for the lifetime of a single request, so every
    service below joins *this* request's transaction and no other's. Returning a
    long-lived instance instead was the defect this replaced.
    """
    factory = _state(request, "unit_of_work_factory", "persistence")
    return factory()


UnitOfWork = Annotated[object, Depends(get_unit_of_work)]


def get_admin(unit_of_work: UnitOfWork) -> HubAdminService:
    return HubAdminService(unit_of_work)


def get_drop_offs(request: Request, unit_of_work: UnitOfWork) -> DropOffService:
    return DropOffService(
        unit_of_work,
        policy=request.app.state.drop_off_policy,
        outbox_max_attempts=request.app.state.settings.outbox_max_attempts,
    )


def get_processing(request: Request, unit_of_work: UnitOfWork) -> HubProcessingService:
    return HubProcessingService(
        unit_of_work,
        outbox_max_attempts=request.app.state.settings.outbox_max_attempts,
    )


def get_linehaul(unit_of_work: UnitOfWork) -> LinehaulService:
    return LinehaulService(unit_of_work)


def get_authorizer(request: Request) -> HubAuthorizer:
    return _state(request, "authorizer", "authorizer")


Admin = Annotated[HubAdminService, Depends(get_admin)]
DropOffs = Annotated[DropOffService, Depends(get_drop_offs)]
Processing = Annotated[HubProcessingService, Depends(get_processing)]
Linehauls = Annotated[LinehaulService, Depends(get_linehaul)]
Authorizer = Annotated[HubAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: HubAuthorizer, header: str | None, command: HubCommand
) -> HubActor:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_bearer_token"})
    token = header.split(" ", 1)[1].strip()
    try:
        decision = await authorizer.authorize(bearer_token=token, command=command)
    except AuthorizerUnavailableError:
        raise HTTPException(
            status_code=503, detail={"code": "authorization_unavailable"}
        ) from None
    if decision.outcome is AuthorizationOutcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    if not decision.allowed or decision.actor is None:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated"})
    return decision.actor


def _require_posted_staff(actor: HubActor, hub_id: UUID) -> None:
    """Hub-floor actions need staff posted to *this* facility."""
    if actor.is_operations:
        return
    if not actor.is_hub_staff or not actor.posted_to(hub_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _require_operations(actor: HubActor) -> None:
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _require_read_access(actor: HubActor, hub_id: UUID) -> None:
    if actor.is_operations or actor.is_support:
        return
    if not actor.posted_to(hub_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


# ------------------------------------------------------------------ hubs


@router.post("/hubs", response_model=HubResponse, status_code=201)
async def register_hub(
    payload: RegisterHubRequest,
    admin: Admin,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> HubResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.HUB_ADMIN
    )
    _require_operations(actor)
    try:
        facility = admin.register_hub(
            code=payload.code,
            name=payload.name,
            governorate=payload.governorate,
            cut_off_local_time=payload.cut_off_local_time,
            vehicle_cameras_fitted=payload.vehicle_cameras_fitted,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _hub_response(facility)


@router.get("/hubs", response_model=list[HubResponse])
async def list_hubs(
    admin: Admin,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[HubResponse]:
    await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.ACTIVITY_READ
    )
    return [_hub_response(facility) for facility in admin.list_hubs()]


@router.patch("/hubs/{hub_id}/cut-off", response_model=HubResponse)
async def set_cut_off(
    hub_id: UUID,
    payload: SetCutOffRequest,
    admin: Admin,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> HubResponse:
    """v6.3 p.23 — each hub's own cut-off, set independently."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.HUB_ADMIN
    )
    _require_operations(actor)
    try:
        facility = admin.set_cut_off(
            hub_id=hub_id, cut_off_local_time=payload.cut_off_local_time
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _hub_response(facility)


# ------------------------------------------------------------------ drop-off


@router.post("/hubs/{hub_id}/drop-offs", response_model=DropOffResponse, status_code=201)
async def expect_drop_off(
    hub_id: UUID,
    payload: ExpectDropOffRequest,
    drop_offs: DropOffs,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DropOffResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.DROP_OFF_REGISTER,
    )
    _require_posted_staff(actor, hub_id)
    try:
        drop_off = drop_offs.expect_drop_off(
            hub_id=hub_id,
            tracking_code=payload.tracking_code,
            shipment_request_id=payload.shipment_request_id,
            sender_principal_id=payload.sender_principal_id,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _drop_off_response(drop_off)


@router.post("/hubs/{hub_id}/drop-offs/details", response_model=DropOffResponse)
async def capture_details(
    hub_id: UUID,
    payload: CaptureDetailsRequest,
    drop_offs: DropOffs,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DropOffResponse:
    """v6.3 p.18 — hub staff take the details when the customer arrives with nothing."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.DROP_OFF_CAPTURE_DETAILS,
    )
    _require_posted_staff(actor, hub_id)
    try:
        drop_off = drop_offs.capture_details(
            hub_id=hub_id,
            tracking_code=payload.tracking_code,
            details=payload.details,
            sender_principal_id=payload.sender_principal_id,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _drop_off_response(drop_off)


@router.post("/drop-offs/{drop_off_id}/label", response_model=DropOffResponse)
async def apply_label(
    drop_off_id: UUID,
    payload: ApplyLabelRequest,
    drop_offs: DropOffs,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DropOffResponse:
    """CUS-05, CUS-07 — hub staff weigh the parcel, label it and scan it."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.DROP_OFF_LABEL
    )
    hub_id = _hub_of_drop_off(drop_offs, drop_off_id)
    _require_posted_staff(actor, hub_id)
    try:
        drop_off = drop_offs.apply_label(
            drop_off_id=drop_off_id,
            label_code=payload.label_code,
            weight_grams=payload.weight_grams,
            operator_principal_id=actor.principal_id,
            # Taken from the proven actor, never from the request body.
            actor_is_hub_staff=actor.is_hub_staff or actor.is_operations,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _drop_off_response(drop_off)


@router.post("/drop-offs/{drop_off_id}/accept", response_model=DropOffResponse)
async def accept_drop_off(
    drop_off_id: UUID,
    payload: AcceptDropOffRequest,
    drop_offs: DropOffs,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DropOffResponse:
    """Custody begins here, exactly as it does at a driver's acceptance scan."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.DROP_OFF_ACCEPT
    )
    hub_id = _hub_of_drop_off(drop_offs, drop_off_id)
    _require_posted_staff(actor, hub_id)
    try:
        result = drop_offs.accept(
            drop_off_id=drop_off_id,
            operator_principal_id=actor.principal_id,
            destination_governorate=payload.destination_governorate,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _drop_off_response(result.drop_off)


@router.get("/hubs/{hub_id}/drop-offs", response_model=list[DropOffResponse])
async def list_open_drop_offs(
    hub_id: UUID,
    drop_offs: DropOffs,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[DropOffResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.DROP_OFF_READ
    )
    _require_read_access(actor, hub_id)
    return [_drop_off_response(item) for item in drop_offs.list_open(hub_id=hub_id)]


def _hub_of_drop_off(drop_offs: DropOffService, drop_off_id: UUID) -> UUID:
    try:
        return drop_offs.get(drop_off_id).hub_id
    except HubError as exc:
        raise_http_for_domain_error(exc)


# ------------------------------------------------------------------ processing


@router.post("/hubs/{hub_id}/parcels/scan-in", response_model=PresenceResponse, status_code=201)
async def scan_in(
    hub_id: UUID,
    payload: ScanInRequest,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PresenceResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.PARCEL_SCAN_IN
    )
    _require_posted_staff(actor, hub_id)
    try:
        presence = processing.scan_in(
            hub_id=hub_id,
            tracking_code=payload.tracking_code,
            destination_governorate=payload.destination_governorate,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _presence_response(presence)


@router.post("/hubs/{hub_id}/parcels/sort", response_model=PresenceResponse)
async def sort_parcel(
    hub_id: UUID,
    payload: SortRequest,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PresenceResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.PARCEL_SORT
    )
    _require_posted_staff(actor, hub_id)
    try:
        presence = processing.sort(
            hub_id=hub_id,
            tracking_code=payload.tracking_code,
            urgency=Urgency(payload.urgency),
            route_code=payload.route_code,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _presence_response(presence)


@router.post("/hubs/{hub_id}/parcels/hold", response_model=HoldResponse)
async def hold_parcel(
    hub_id: UUID,
    payload: HoldRequest,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> HoldResponse:
    """Stopping a parcel, including the checkpoint interception of SHP-13."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.PARCEL_HOLD
    )
    _require_posted_staff(actor, hub_id)
    try:
        outcome = processing.hold(
            hub_id=hub_id,
            tracking_code=payload.tracking_code,
            reason=HoldReason(payload.reason),
            reported_by_actor_id=actor.principal_id,
            consignment_id=payload.consignment_id,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _hold_response(outcome)


@router.post("/hubs/{hub_id}/parcels/handover", response_model=PresenceResponse)
async def hand_to_last_mile(
    hub_id: UUID,
    payload: HandoverRequest,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PresenceResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.PARCEL_HANDOVER
    )
    _require_posted_staff(actor, hub_id)
    try:
        presence = processing.hand_to_last_mile(
            hub_id=hub_id, tracking_code=payload.tracking_code
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _presence_response(presence)


@router.get("/hubs/{hub_id}/parcels", response_model=list[PresenceResponse])
async def list_parcels(
    hub_id: UUID,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[PresenceResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.PARCEL_READ
    )
    _require_read_access(actor, hub_id)
    return [_presence_response(item) for item in processing.list_parcels(hub_id=hub_id)]


@router.get("/hubs/{hub_id}/activity", response_model=ActivityResponse)
async def hub_activity(
    hub_id: UUID,
    processing: Processing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ActivityResponse:
    """OPS-05 — backlog, ready, delayed and missing, in one read."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.ACTIVITY_READ
    )
    _require_read_access(actor, hub_id)
    return _activity_response(processing.activity(hub_id=hub_id))


# ------------------------------------------------------------------ consignment


@router.post(
    "/hubs/{hub_id}/consignments", response_model=ConsignmentResponse, status_code=201
)
async def open_consignment(
    hub_id: UUID,
    payload: OpenConsignmentRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_CREATE,
    )
    _require_posted_staff(actor, hub_id)
    try:
        consignment = linehauls.open_consignment(
            origin_hub_id=hub_id, destination_hub_id=payload.destination_hub_id
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


@router.post("/consignments/{consignment_id}/parcels", response_model=ConsignmentResponse)
async def add_parcel_to_consignment(
    consignment_id: UUID,
    payload: AddParcelRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_CREATE,
    )
    _require_posted_staff(actor, _origin_of(linehauls, consignment_id))
    try:
        consignment = linehauls.add_parcel(
            consignment_id=consignment_id, tracking_code=payload.tracking_code
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


@router.post("/consignments/{consignment_id}/seal", response_model=ConsignmentResponse)
async def seal_consignment(
    consignment_id: UUID,
    payload: SealRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    """Optional, at the hub's discretion (v6.3 p.22)."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.CONSIGNMENT_SEAL
    )
    _require_posted_staff(actor, _origin_of(linehauls, consignment_id))
    try:
        consignment = linehauls.apply_seal(
            consignment_id=consignment_id,
            seal_code=payload.seal_code,
            operator_principal_id=actor.principal_id,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


@router.post("/consignments/{consignment_id}/dispatch", response_model=ConsignmentResponse)
async def dispatch_consignment(
    consignment_id: UUID,
    payload: DispatchRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    """SHP-07 — after this hub's own cut-off. Overriding it is an Operations decision."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_DISPATCH,
    )
    _require_posted_staff(actor, _origin_of(linehauls, consignment_id))
    if payload.override_cut_off and not actor.is_operations:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "cut_off_override_needs_operations",
                "message": "only Operations may dispatch before a hub's cut-off",
            },
        )
    try:
        consignment = linehauls.dispatch(
            consignment_id=consignment_id, override_cut_off=payload.override_cut_off
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


@router.post("/consignments/{consignment_id}/arrival", response_model=ConsignmentResponse)
async def record_arrival(
    consignment_id: UUID,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_RECEIVE,
    )
    _require_posted_staff(actor, _destination_of(linehauls, consignment_id))
    try:
        consignment = linehauls.record_arrival(consignment_id=consignment_id)
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


@router.post("/consignments/{consignment_id}/seal-check", response_model=SealCheckResponse)
async def check_seal(
    consignment_id: UUID,
    payload: SealCheckRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> SealCheckResponse:
    """SHP-08 — a mismatch opens an investigation, never a silent pass-through."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_RECEIVE,
    )
    _require_posted_staff(actor, _destination_of(linehauls, consignment_id))
    try:
        result = linehauls.check_seal(
            consignment_id=consignment_id,
            observed_seal_code=payload.observed_seal_code,
            operator_principal_id=actor.principal_id,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _seal_check_response(result)


@router.post("/consignments/{consignment_id}/reconcile", response_model=ConsignmentResponse)
async def reconcile_consignment(
    consignment_id: UUID,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConsignmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=HubCommand.CONSIGNMENT_RECEIVE,
    )
    _require_posted_staff(actor, _destination_of(linehauls, consignment_id))
    try:
        consignment = linehauls.reconcile(consignment_id=consignment_id)
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _consignment_response(consignment)


def _origin_of(linehauls: LinehaulService, consignment_id: UUID) -> UUID:
    try:
        return linehauls.get_consignment(consignment_id).origin_hub_id
    except HubError as exc:
        raise_http_for_domain_error(exc)


def _destination_of(linehauls: LinehaulService, consignment_id: UUID) -> UUID:
    try:
        return linehauls.get_consignment(consignment_id).destination_hub_id
    except HubError as exc:
        raise_http_for_domain_error(exc)


# ------------------------------------------------------------------ linehaul


@router.post(
    "/consignments/{consignment_id}/linehaul",
    response_model=LinehaulResponse,
    status_code=201,
)
async def plan_linehaul(
    consignment_id: UUID,
    payload: PlanLinehaulRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> LinehaulResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.LINEHAUL_PLAN
    )
    _require_posted_staff(actor, _origin_of(linehauls, consignment_id))
    try:
        linehaul = linehauls.plan_linehaul(
            consignment_id=consignment_id,
            vehicle_reference=payload.vehicle_reference,
            driver_principal_id=payload.driver_principal_id,
            planned_departure_at=payload.planned_departure_at,
            expected_arrival_at=payload.expected_arrival_at,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _linehaul_response(linehaul)


@router.post("/linehauls/{linehaul_id}/depart", response_model=LinehaulResponse)
async def depart_linehaul(
    linehaul_id: UUID,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> LinehaulResponse:
    await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.LINEHAUL_REPORT
    )
    try:
        linehaul = linehauls.depart(linehaul_id=linehaul_id)
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _linehaul_response(linehaul)


@router.post("/linehauls/{linehaul_id}/positions", response_model=PositionResponse, status_code=201)
async def record_position(
    linehaul_id: UUID,
    payload: PositionRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PositionResponse:
    """OPS-01 — the vehicle tracker and the driver's device are separate sources."""
    await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.LINEHAUL_REPORT
    )
    try:
        position = linehauls.record_position(
            linehaul_id=linehaul_id,
            source=PositionSource(payload.source),
            point=GeoPoint(latitude=payload.latitude, longitude=payload.longitude),
            recorded_at=payload.recorded_at,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _position_response(position)


@router.post("/linehauls/{linehaul_id}/deviation", response_model=LinehaulResponse)
async def flag_deviation(
    linehaul_id: UUID,
    payload: DeviationRequest,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> LinehaulResponse:
    """OPS-02 — the driver reports deviation; the ETA is revised with it."""
    await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.LINEHAUL_REPORT
    )
    try:
        linehaul = linehauls.flag_deviation(
            linehaul_id=linehaul_id,
            note=payload.note,
            revised_arrival_at=payload.revised_arrival_at,
        )
    except HubError as exc:
        raise_http_for_domain_error(exc)
    return _linehaul_response(linehaul)


@router.get("/linehauls/{linehaul_id}/positions", response_model=list[PositionResponse])
async def list_positions(
    linehaul_id: UUID,
    linehauls: Linehauls,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[PositionResponse]:
    await _authorize(
        authorizer=authorizer, header=authorization, command=HubCommand.LINEHAUL_READ
    )
    return [
        _position_response(item)
        for item in linehauls.positions_for(linehaul_id=linehaul_id)
    ]


# ------------------------------------------------------------------ responses


def _hub_response(facility: Hub) -> HubResponse:
    return HubResponse(
        hub_id=facility.hub_id,
        code=facility.code,
        name=facility.name,
        governorate=facility.governorate,
        cut_off_local_time=facility.cut_off.local_time,
        is_active=facility.is_active,
        vehicle_cameras_fitted=facility.vehicle_cameras_fitted,
        version=facility.version,
    )


def _drop_off_response(drop_off: DropOff) -> DropOffResponse:
    return DropOffResponse(
        drop_off_id=drop_off.drop_off_id,
        hub_id=drop_off.hub_id,
        tracking_code=drop_off.tracking_code,
        status=drop_off.status.value,
        weight_grams=drop_off.weight_grams,
        label_code=drop_off.label_code,
        expires_at=drop_off.expires_at,
        accepted_at=drop_off.accepted_at,
        version=drop_off.version,
    )


def _presence_response(presence: ParcelPresence) -> PresenceResponse:
    return PresenceResponse(
        presence_id=presence.presence_id,
        hub_id=presence.hub_id,
        tracking_code=presence.tracking_code,
        status=presence.status.value,
        destination_governorate=presence.destination_governorate,
        urgency=presence.urgency.value,
        routing_decision=(
            presence.routing_decision.value
            if presence.routing_decision is not None
            else None
        ),
        route_code=presence.route_code,
        consignment_id=presence.consignment_id,
        hold_reason=(
            presence.hold_reason.value if presence.hold_reason is not None else None
        ),
        disposition=(
            presence.disposition.value if presence.disposition is not None else None
        ),
        version=presence.version,
    )


def _hold_response(outcome: HoldOutcome) -> HoldResponse:
    return HoldResponse(
        parcel=_presence_response(outcome.presence),
        disposition=outcome.disposition.value,
    )


def _activity_response(snapshot: HubActivitySnapshot) -> ActivityResponse:
    return ActivityResponse(
        hub_id=snapshot.hub_id,
        received=snapshot.received,
        sorted=snapshot.sorted_count,
        grouped=snapshot.grouped,
        ready_for_last_mile=snapshot.ready_for_last_mile,
        held=snapshot.held,
        awaiting_linehaul=snapshot.awaiting_linehaul,
        delayed_linehauls=snapshot.delayed_linehauls,
        backlog=snapshot.backlog,
    )


def _consignment_response(consignment: Consignment) -> ConsignmentResponse:
    return ConsignmentResponse(
        consignment_id=consignment.consignment_id,
        origin_hub_id=consignment.origin_hub_id,
        destination_hub_id=consignment.destination_hub_id,
        status=consignment.status.value,
        seal_code=consignment.seal.seal_code if consignment.seal else None,
        parcel_codes=list(consignment.parcel_codes),
        parcel_count=consignment.parcel_count,
        version=consignment.version,
    )


def _seal_check_response(result: SealCheckResult) -> SealCheckResponse:
    return SealCheckResponse(
        check_id=result.check.check_id,
        consignment_id=result.check.consignment_id,
        outcome=result.check.outcome.value,
        opened_investigation=result.opened_investigation,
        consignment=_consignment_response(result.consignment),
    )


def _linehaul_response(linehaul: Linehaul) -> LinehaulResponse:
    return LinehaulResponse(
        linehaul_id=linehaul.linehaul_id,
        consignment_id=linehaul.consignment_id,
        origin_hub_id=linehaul.origin_hub_id,
        destination_hub_id=linehaul.destination_hub_id,
        vehicle_reference=linehaul.vehicle_reference,
        status=linehaul.status.value,
        departed_at=linehaul.departed_at,
        expected_arrival_at=linehaul.expected_arrival_at,
        arrived_at=linehaul.arrived_at,
        route_deviation_flagged=linehaul.route_deviation_flagged,
        deviation_note=linehaul.deviation_note,
        version=linehaul.version,
    )


def _position_response(position: VehiclePosition) -> PositionResponse:
    return PositionResponse(
        position_id=position.position_id,
        linehaul_id=position.linehaul_id,
        source=position.source.value,
        latitude=position.point.latitude,
        longitude=position.point.longitude,
        recorded_at=position.recorded_at,
    )
