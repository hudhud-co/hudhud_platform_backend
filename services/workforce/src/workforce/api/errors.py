"""Domain error to HTTP mapping for the Workforce service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from workforce.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationNotFound,
    ApplicationTransitionNotAllowed,
    BlockAlreadyCleared,
    BlockNotFound,
    DocumentsIncomplete,
    DriverNotAssignable,
    DriverNotFound,
    InvalidVehicleDetails,
    LeaveDecisionNoteRequired,
    LeaveRequestNotFound,
    LeaveTransitionNotAllowed,
    NoShiftScheduled,
    OfficeVerificationRequired,
    OnlySupportMayClearABlock,
    OnlySupportMayDecideLeave,
    OverlappingLeaveRequest,
    ShiftAlreadyStarted,
    ShiftSelectionRequired,
    StaleWorkforceRecord,
    TermsNotAccepted,
    WorkforceError,
)

_STATUS: dict[type[WorkforceError], int] = {
    ApplicationNotFound: 404,
    DriverNotFound: 404,
    LeaveRequestNotFound: 404,
    BlockNotFound: 404,
    ApplicationAlreadyOpen: 409,
    ApplicationTransitionNotAllowed: 409,
    # 422: the operator has not finished the checklist, which is a request problem.
    DocumentsIncomplete: 422,
    OfficeVerificationRequired: 409,
    ShiftSelectionRequired: 422,
    TermsNotAccepted: 422,
    NoShiftScheduled: 409,
    ShiftAlreadyStarted: 409,
    DriverNotAssignable: 409,
    BlockAlreadyCleared: 409,
    # 403: the request is well formed; this actor is the wrong one (OPS-09).
    OnlySupportMayClearABlock: 403,
    OnlySupportMayDecideLeave: 403,
    LeaveTransitionNotAllowed: 409,
    OverlappingLeaveRequest: 409,
    LeaveDecisionNoteRequired: 422,
    InvalidVehicleDetails: 422,
    StaleWorkforceRecord: 409,
}

_CODE: dict[type[WorkforceError], str] = {
    ApplicationNotFound: "application_not_found",
    DriverNotFound: "driver_not_found",
    LeaveRequestNotFound: "leave_request_not_found",
    BlockNotFound: "block_not_found",
    ApplicationAlreadyOpen: "application_already_open",
    ApplicationTransitionNotAllowed: "application_transition_not_allowed",
    DocumentsIncomplete: "documents_incomplete",
    OfficeVerificationRequired: "office_verification_required",
    ShiftSelectionRequired: "shift_selection_required",
    TermsNotAccepted: "terms_not_accepted",
    NoShiftScheduled: "no_shift_scheduled",
    ShiftAlreadyStarted: "shift_already_started",
    DriverNotAssignable: "driver_not_assignable",
    BlockAlreadyCleared: "block_already_cleared",
    OnlySupportMayClearABlock: "only_support_may_clear_a_block",
    OnlySupportMayDecideLeave: "only_support_may_decide_leave",
    LeaveTransitionNotAllowed: "leave_transition_not_allowed",
    OverlappingLeaveRequest: "overlapping_leave_request",
    LeaveDecisionNoteRequired: "leave_decision_note_required",
    InvalidVehicleDetails: "invalid_vehicle_details",
    StaleWorkforceRecord: "concurrent_modification",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, WorkforceError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "workforce_error"),
            "message": str(exc),
        }
        if isinstance(exc, DocumentsIncomplete):
            detail["missing"] = list(exc.missing)
        if isinstance(exc, DriverNotAssignable):
            detail["reasons"] = list(exc.reasons)
        if isinstance(exc, OverlappingLeaveRequest):
            detail["reference"] = exc.reference
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
