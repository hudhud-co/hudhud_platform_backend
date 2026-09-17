"""Domain error to HTTP mapping for the Hub service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from hub.domain.errors import (
    CashOnDeliveryNotAvailableAtDropOff,
    ConsignmentAlreadySealed,
    ConsignmentIsEmpty,
    ConsignmentNotFound,
    ConsignmentTransitionNotAllowed,
    CustomerMayNotLabelTheirOwnParcel,
    CutOffNotReached,
    DropOffDetailsRequired,
    DropOffExpired,
    DropOffNotFound,
    DropOffNotLabelled,
    DropOffTransitionNotAllowed,
    HubError,
    HubNotActive,
    HubNotFound,
    InvalidLabelCode,
    LinehaulDriverMayNotSplitABatch,
    LinehaulNotFound,
    LinehaulTransitionNotAllowed,
    ParcelAlreadyReceived,
    ParcelIsHeld,
    ParcelNotInThisHub,
    ParcelNotSorted,
    SameCityParcelDoesNotTravelBetweenHubs,
    SealCodeInvalid,
    SealMismatchRequiresInvestigation,
    StaleHubRecord,
    UnknownGovernorate,
    UnsealedConsignmentHasNothingToCheck,
    WeightRequiredAtDropOff,
)

_STATUS: dict[type[HubError], int] = {
    HubNotFound: 404,
    HubNotActive: 409,
    DropOffNotFound: 404,
    ParcelNotInThisHub: 404,
    ConsignmentNotFound: 404,
    LinehaulNotFound: 404,
    DropOffTransitionNotAllowed: 409,
    # 403, not 422: the request is well formed; this actor is the wrong one (CUS-05).
    CustomerMayNotLabelTheirOwnParcel: 403,
    DropOffDetailsRequired: 422,
    DropOffNotLabelled: 409,
    DropOffExpired: 409,
    WeightRequiredAtDropOff: 422,
    CashOnDeliveryNotAvailableAtDropOff: 409,
    ParcelAlreadyReceived: 409,
    ParcelNotSorted: 409,
    ParcelIsHeld: 409,
    SameCityParcelDoesNotTravelBetweenHubs: 409,
    ConsignmentTransitionNotAllowed: 409,
    ConsignmentIsEmpty: 409,
    ConsignmentAlreadySealed: 409,
    SealCodeInvalid: 422,
    UnsealedConsignmentHasNothingToCheck: 409,
    SealMismatchRequiresInvestigation: 409,
    CutOffNotReached: 409,
    LinehaulTransitionNotAllowed: 409,
    LinehaulDriverMayNotSplitABatch: 403,
    UnknownGovernorate: 422,
    InvalidLabelCode: 422,
    StaleHubRecord: 409,
}

_CODE: dict[type[HubError], str] = {
    HubNotFound: "hub_not_found",
    HubNotActive: "hub_not_active",
    DropOffNotFound: "drop_off_not_found",
    ParcelNotInThisHub: "parcel_not_in_this_hub",
    ConsignmentNotFound: "consignment_not_found",
    LinehaulNotFound: "linehaul_not_found",
    DropOffTransitionNotAllowed: "drop_off_transition_not_allowed",
    CustomerMayNotLabelTheirOwnParcel: "hub_staff_label_the_parcel",
    DropOffDetailsRequired: "drop_off_details_required",
    DropOffNotLabelled: "drop_off_not_labelled",
    DropOffExpired: "drop_off_expired",
    WeightRequiredAtDropOff: "weight_required_at_drop_off",
    CashOnDeliveryNotAvailableAtDropOff: "cod_not_available_at_drop_off",
    ParcelAlreadyReceived: "parcel_already_received",
    ParcelNotSorted: "parcel_not_sorted",
    ParcelIsHeld: "parcel_is_held",
    SameCityParcelDoesNotTravelBetweenHubs: "same_city_parcel_skips_linehaul",
    ConsignmentTransitionNotAllowed: "consignment_transition_not_allowed",
    ConsignmentIsEmpty: "consignment_is_empty",
    ConsignmentAlreadySealed: "consignment_already_sealed",
    SealCodeInvalid: "seal_code_invalid",
    UnsealedConsignmentHasNothingToCheck: "consignment_not_sealed",
    SealMismatchRequiresInvestigation: "seal_mismatch_requires_investigation",
    CutOffNotReached: "cut_off_not_reached",
    LinehaulTransitionNotAllowed: "linehaul_transition_not_allowed",
    LinehaulDriverMayNotSplitABatch: "linehaul_driver_may_not_split_batch",
    UnknownGovernorate: "unknown_governorate",
    InvalidLabelCode: "invalid_label_code",
    StaleHubRecord: "concurrent_modification",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, HubError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "hub_error"),
            "message": str(exc),
        }
        if isinstance(exc, DropOffDetailsRequired):
            detail["missing"] = list(exc.missing)
        if isinstance(exc, CutOffNotReached):
            detail["hub"] = exc.hub_code
            detail["cut_off"] = exc.cut_off
        if isinstance(exc, ParcelIsHeld):
            detail["hold_reason"] = exc.reason
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
