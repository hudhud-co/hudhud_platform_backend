"""Workforce HTTP adapter.

The recurring rule is OPS-09: a driver may ask, and support decides. A driver requests an
unblock and raises leave; only support or operations clears the block or approves the
leave. That distinction is enforced here *and* in the domain, so removing this check alone
would not open the door.

The eligibility route is the one other services call. It is the reason this is a service.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from workforce.api.errors import raise_http_for_domain_error
from workforce.api.schemas import (
    ApplicationResponse,
    AttendanceResponse,
    BlockResponse,
    ClearBlockRequest,
    DecideLeaveRequest,
    DriverResponse,
    EligibilityResponse,
    LeaveResponse,
    RejectApplicationRequest,
    RequestLeaveRequest,
    SetShiftPatternRequest,
    ShiftPatternResponse,
    ShiftWindowModel,
    StartShiftResponse,
    SubmitApplicationRequest,
    VehicleModel,
    VerifyApplicationRequest,
)
from workforce.application.attendance_service import (
    AttendanceService,
    LatenessPolicy,
    ShiftStartResult,
)
from workforce.application.eligibility_service import EligibilityService
from workforce.application.leave_service import LeaveService
from workforce.application.onboarding_service import (
    ApplicationDraft,
    OnboardingService,
)
from workforce.domain.entities import (
    AssignmentEligibility,
    AttendanceRecord,
    DriverApplication,
    DriverProfile,
    LatenessBlock,
    LeaveRequest,
    ShiftPattern,
)
from workforce.domain.errors import (
    DriverNotFound,
    InvalidVehicleDetails,
    WorkforceError,
)
from workforce.domain.value_objects import (
    LeaveKind,
    LeaveReason,
    LeaveWindow,
    ShiftSlot,
    ShiftWindow,
    VehicleDetails,
    Weekday,
)
from workforce.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    WorkforceActor,
    WorkforceAuthorizer,
    WorkforceCommand,
)

router = APIRouter(prefix="/workforce", tags=["workforce"])

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


def get_onboarding(unit_of_work: UnitOfWork) -> OnboardingService:
    return OnboardingService(unit_of_work)


def get_attendance(request: Request, unit_of_work: UnitOfWork) -> AttendanceService:
    settings = request.app.state.settings
    return AttendanceService(
        unit_of_work,
        policy=LatenessPolicy(
            grace_minutes=settings.lateness_grace_minutes,
            block_after_minutes=settings.lateness_block_after_minutes,
        ),
    )


def get_leave(unit_of_work: UnitOfWork) -> LeaveService:
    return LeaveService(unit_of_work)


def get_eligibility(request: Request, unit_of_work: UnitOfWork) -> EligibilityService:
    return EligibilityService(
        unit_of_work,
        require_started_shift=(
            request.app.state.settings.require_started_shift_for_assignment
        ),
    )


def get_authorizer(request: Request) -> WorkforceAuthorizer:
    return _state(request, "authorizer", "authorizer")


Onboarding = Annotated[OnboardingService, Depends(get_onboarding)]
Attendance = Annotated[AttendanceService, Depends(get_attendance)]
Leave = Annotated[LeaveService, Depends(get_leave)]
Eligibility = Annotated[EligibilityService, Depends(get_eligibility)]
Authorizer = Annotated[WorkforceAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: WorkforceAuthorizer, header: str | None, command: WorkforceCommand
) -> WorkforceActor:
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


def _require_operations(actor: WorkforceActor) -> None:
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _own_driver(onboarding: OnboardingService, actor: WorkforceActor) -> DriverProfile:
    """The driver the caller *is*. There is no route to act as another driver."""
    driver = onboarding.find_driver(principal_id=actor.principal_id)
    if driver is None:
        raise_http_for_domain_error(DriverNotFound(str(actor.principal_id)))
    return driver


# ------------------------------------------------------------------ onboarding


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
async def submit_application(
    payload: SubmitApplicationRequest,
    onboarding: Onboarding,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    """SEC-03 — submitting does **not** make anyone assignable."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.APPLICATION_SUBMIT,
    )
    try:
        application = onboarding.submit_application(
            applicant_principal_id=actor.principal_id,
            draft=ApplicationDraft(
                full_name=payload.full_name,
                vehicle=_vehicle(payload.vehicle),
                declared_shifts=tuple(
                    _window(item) for item in payload.declared_shifts
                ),
                terms_version=payload.terms_version,
                documents_received=tuple(payload.documents_received),
            ),
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
async def read_application(
    application_id: UUID,
    onboarding: Onboarding,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.APPLICATION_READ,
    )
    try:
        application = onboarding.get_application(application_id)
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    if (
        application.applicant_principal_id != actor.principal_id
        and not actor.may_decide_for_drivers
    ):
        # 404 rather than 403 — confirming the id exists would leak someone else's case.
        raise HTTPException(status_code=404, detail={"code": "application_not_found"})
    return _application_response(application)


@router.post("/applications/{application_id}/verify", response_model=DriverResponse)
async def verify_application(
    application_id: UUID,
    payload: VerifyApplicationRequest,
    onboarding: Onboarding,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DriverResponse:
    """The office check. Operations only — this is the gate SEC-03 describes."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.APPLICATION_VERIFY,
    )
    _require_operations(actor)
    try:
        result = onboarding.record_office_verification(
            application_id=application_id,
            operator_principal_id=actor.principal_id,
            office=payload.office,
            documents_verified=tuple(payload.documents_verified),
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _driver_response(result.driver)


@router.post("/applications/{application_id}/reject", response_model=ApplicationResponse)
async def reject_application(
    application_id: UUID,
    payload: RejectApplicationRequest,
    onboarding: Onboarding,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.APPLICATION_REJECT,
    )
    _require_operations(actor)
    try:
        application = onboarding.reject(
            application_id=application_id,
            operator_principal_id=actor.principal_id,
            reason=payload.reason,
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


# ------------------------------------------------------------------ shifts


@router.get("/me/driver", response_model=DriverResponse)
async def read_own_driver(
    onboarding: Onboarding,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DriverResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.DRIVER_READ
    )
    return _driver_response(_own_driver(onboarding, actor))


@router.put("/me/shift-pattern", response_model=ShiftPatternResponse)
async def set_shift_pattern(
    payload: SetShiftPatternRequest,
    onboarding: Onboarding,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShiftPatternResponse:
    """DRV-A04 — "Changes apply from tomorrow"."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.SHIFT_PATTERN_UPDATE,
    )
    driver = _own_driver(onboarding, actor)
    try:
        pattern = attendance.set_shift_pattern(
            driver_id=driver.driver_id,
            windows=tuple(_window(item) for item in payload.windows),
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _pattern_response(pattern)


@router.post("/me/shift/start", response_model=StartShiftResponse)
async def start_shift(
    onboarding: Onboarding,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StartShiftResponse:
    """DRV-A01 — "Starting a shift records your attendance"."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.SHIFT_START
    )
    driver = _own_driver(onboarding, actor)
    try:
        result = attendance.start_shift(driver_id=driver.driver_id)
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _start_shift_response(result)


@router.get("/me/attendance", response_model=list[AttendanceResponse])
async def read_own_attendance(
    onboarding: Onboarding,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[AttendanceResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.DRIVER_READ
    )
    driver = _own_driver(onboarding, actor)
    return [
        _attendance_response(record)
        for record in attendance.attendance_for(driver_id=driver.driver_id)
    ]


# ------------------------------------------------------------------ blocks


@router.get("/me/block", response_model=BlockResponse | None)
async def read_own_block(
    onboarding: Onboarding,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> BlockResponse | None:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.BLOCK_READ
    )
    driver = _own_driver(onboarding, actor)
    block = attendance.active_block(driver_id=driver.driver_id)
    return _block_response(block) if block is not None else None


@router.post("/me/block/unblock-request", response_model=BlockResponse)
async def request_unblock(
    onboarding: Onboarding,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> BlockResponse:
    """The driver asks. Support decides (OPS-09)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.BLOCK_REQUEST_UNBLOCK,
    )
    driver = _own_driver(onboarding, actor)
    try:
        block = attendance.request_unblock(driver_id=driver.driver_id)
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _block_response(block)


@router.get("/blocks", response_model=list[BlockResponse])
async def list_active_blocks(
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[BlockResponse]:
    """Support's queue of paused drivers."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.BLOCK_READ
    )
    if not actor.may_decide_for_drivers:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return [_block_response(block) for block in attendance.list_active_blocks()]


@router.post("/blocks/{block_id}/clear", response_model=BlockResponse)
async def clear_block(
    block_id: UUID,
    payload: ClearBlockRequest,
    attendance: Attendance,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> BlockResponse:
    """OPS-09 — support clears the record, never the driver."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.BLOCK_CLEAR
    )
    try:
        block = attendance.clear_block(
            block_id=block_id,
            clearing_actor_id=actor.principal_id,
            # Passed through rather than checked only here, so the rule also holds for
            # any other caller of the service.
            actor_may_decide=actor.may_decide_for_drivers,
            note=payload.note,
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _block_response(block)


# ------------------------------------------------------------------ leave


@router.post("/me/leave", response_model=LeaveResponse, status_code=201)
async def request_leave(
    payload: RequestLeaveRequest,
    onboarding: Onboarding,
    leave: Leave,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> LeaveResponse:
    """DRV-A03 — no lateness penalty applies while the request is pending."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.LEAVE_REQUEST,
    )
    driver = _own_driver(onboarding, actor)
    try:
        window = LeaveWindow(
            kind=LeaveKind(payload.kind),
            start_date=payload.start_date,
            end_date=payload.end_date,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_leave_window", "message": str(exc)}
        ) from None
    try:
        request = leave.request_leave(
            driver_id=driver.driver_id,
            reason=LeaveReason(payload.reason),
            window=window,
            note=payload.note,
        )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _leave_response(request)


@router.get("/me/leave", response_model=list[LeaveResponse])
async def list_own_leave(
    onboarding: Onboarding,
    leave: Leave,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[LeaveResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.LEAVE_READ
    )
    driver = _own_driver(onboarding, actor)
    return [
        _leave_response(item)
        for item in leave.list_for_driver(driver_id=driver.driver_id)
    ]


@router.get("/leave/pending", response_model=list[LeaveResponse])
async def list_pending_leave(
    leave: Leave,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[LeaveResponse]:
    """Support's queue of requests to decide."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.LEAVE_READ
    )
    if not actor.may_decide_for_drivers:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return [_leave_response(item) for item in leave.list_pending()]


@router.post("/leave/{leave_id}/decision", response_model=LeaveResponse)
async def decide_leave(
    leave_id: UUID,
    payload: DecideLeaveRequest,
    leave: Leave,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> LeaveResponse:
    """OPS-09 — support decides. A driver cannot approve their own leave."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=WorkforceCommand.LEAVE_DECIDE
    )
    try:
        if payload.approve:
            request = leave.approve(
                leave_id=leave_id,
                decider_principal_id=actor.principal_id,
                actor_may_decide=actor.may_decide_for_drivers,
            )
        else:
            request = leave.decline(
                leave_id=leave_id,
                decider_principal_id=actor.principal_id,
                actor_may_decide=actor.may_decide_for_drivers,
                note=payload.note or "",
            )
    except WorkforceError as exc:
        raise_http_for_domain_error(exc)
    return _leave_response(request)


# ------------------------------------------------------------------ eligibility


@router.get("/principals/{principal_id}/eligibility", response_model=EligibilityResponse)
async def read_eligibility(
    principal_id: UUID,
    eligibility: Eligibility,
    authorizer: Authorizer,
    day: date | None = None,
    authorization: BearerHeader = None,
) -> EligibilityResponse:
    """The route Pickup and Delivery call before assigning work.

    A principal may read their own; anyone else needs support or operations, which is
    what a service credential is issued for.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=WorkforceCommand.ELIGIBILITY_QUERY,
    )
    if actor.principal_id != principal_id and not actor.may_decide_for_drivers:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return _eligibility_response(
        eligibility.for_principal(principal_id=principal_id, day=day)
    )


# ------------------------------------------------------------------ conversions


def _vehicle(model: VehicleModel) -> VehicleDetails:
    try:
        return VehicleDetails(
            kind=model.kind, plate_number=model.plate_number, model=model.model
        )
    except ValueError as exc:
        raise_http_for_domain_error(InvalidVehicleDetails(str(exc)))


def _window(model: ShiftWindowModel) -> ShiftWindow:
    try:
        return ShiftWindow(
            weekday=Weekday(model.weekday),
            slot=ShiftSlot(model.slot),
            starts_at=model.starts_at,
            ends_at=model.ends_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_shift_window", "message": str(exc)}
        ) from None


def _window_model(window: ShiftWindow) -> ShiftWindowModel:
    return ShiftWindowModel(
        weekday=int(window.weekday),
        slot=window.slot.value,
        starts_at=window.starts_at,
        ends_at=window.ends_at,
    )


def _application_response(application: DriverApplication) -> ApplicationResponse:
    return ApplicationResponse(
        application_id=application.application_id,
        reference=application.reference,
        status=application.status.value,
        full_name=application.full_name,
        awaits_office_visit=application.awaits_office_visit,
        documents_received=list(application.documents_received),
        documents_verified=list(application.documents_verified),
        verified_at_office=application.verified_at_office,
        decision_reason=application.decision_reason,
        version=application.version,
    )


def _driver_response(driver: DriverProfile) -> DriverResponse:
    return DriverResponse(
        driver_id=driver.driver_id,
        principal_id=driver.principal_id,
        full_name=driver.full_name,
        vehicle=VehicleModel(
            kind=driver.vehicle.kind,
            plate_number=driver.vehicle.plate_number,
            model=driver.vehicle.model,
        ),
        status=driver.status.value,
        version=driver.version,
    )


def _pattern_response(pattern: ShiftPattern) -> ShiftPatternResponse:
    return ShiftPatternResponse(
        pattern_id=pattern.pattern_id,
        driver_id=pattern.driver_id,
        windows=[_window_model(window) for window in pattern.windows],
        effective_from=pattern.effective_from,
        effective_to=pattern.effective_to,
        version=pattern.version,
    )


def _attendance_response(record: AttendanceRecord) -> AttendanceResponse:
    return AttendanceResponse(
        attendance_id=record.attendance_id,
        shift_date=record.shift_date,
        scheduled_start=record.scheduled_start,
        status=record.status.value,
        started_at=record.started_at,
        lateness_minutes=record.lateness_minutes,
    )


def _block_response(block: LatenessBlock) -> BlockResponse:
    return BlockResponse(
        block_id=block.block_id,
        driver_id=block.driver_id,
        reason=block.reason.value,
        shift_date=block.shift_date,
        scheduled_start=block.scheduled_start,
        delay_minutes=block.delay_minutes,
        blocked_since=block.blocked_since,
        unblock_requested=block.unblock_requested,
        cleared_at=block.cleared_at,
        clearing_note=block.clearing_note,
    )


def _start_shift_response(result: ShiftStartResult) -> StartShiftResponse:
    return StartShiftResponse(
        attendance=_attendance_response(result.attendance),
        block=_block_response(result.block) if result.block is not None else None,
        penalty_waived_by_leave=result.penalty_waived_by_leave,
    )


def _leave_response(request: LeaveRequest) -> LeaveResponse:
    return LeaveResponse(
        leave_id=request.leave_id,
        reference=request.reference,
        reason=request.reason.value,
        kind=request.window.kind.value,
        start_date=request.window.start_date,
        end_date=request.window.end_date,
        starts_at=request.window.starts_at,
        ends_at=request.window.ends_at,
        status=request.status.value,
        note=request.note,
        decision_note=request.decision_note,
        version=request.version,
    )


def _eligibility_response(eligibility: AssignmentEligibility) -> EligibilityResponse:
    return EligibilityResponse(
        principal_id=eligibility.principal_id,
        driver_id=eligibility.driver_id,
        eligible=eligibility.eligible,
        reasons=list(eligibility.reasons),
        shift_started=eligibility.shift_started,
    )
