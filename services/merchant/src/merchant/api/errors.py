"""Domain error to HTTP mapping for the Merchant service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from merchant.domain.errors import (
    ApplicationAlreadyOpen,
    ApplicationAttributesMissing,
    ApplicationDataSetNotDefined,
    ApplicationNotFound,
    ApplicationTransitionNotAllowed,
    CategoryInUse,
    CategoryNotFound,
    DecisionReasonRequired,
    ForbiddenTeamCapability,
    InvalidPhoneNumber,
    LabelCountInvalid,
    LabelStockExhausted,
    LastStoreCannotBeArchived,
    MembershipAlreadyExists,
    MembershipNotFound,
    MembershipNotPending,
    MembershipStoresRequired,
    MerchantCodeAlreadyIssued,
    MerchantError,
    MerchantNotActive,
    MerchantNotFound,
    PrinterAuthorizationNotFound,
    ProductNameRequired,
    ProductNotFound,
    SelfPrintingNotAuthorized,
    SenderMayNotSetPlatformPolicy,
    StaleMerchantRecord,
    StoreArchived,
    StoreNotFound,
    UnknownGovernorate,
)

_STATUS: dict[type[MerchantError], int] = {
    ApplicationNotFound: 404,
    ApplicationAlreadyOpen: 409,
    ApplicationTransitionNotAllowed: 409,
    # 409, not 422: the request is well formed and the platform is not ready for it.
    ApplicationDataSetNotDefined: 409,
    ApplicationAttributesMissing: 422,
    DecisionReasonRequired: 422,
    MerchantNotFound: 404,
    MerchantNotActive: 409,
    MerchantCodeAlreadyIssued: 409,
    SenderMayNotSetPlatformPolicy: 403,
    StoreNotFound: 404,
    StoreArchived: 409,
    LastStoreCannotBeArchived: 409,
    UnknownGovernorate: 422,
    MembershipNotFound: 404,
    MembershipAlreadyExists: 409,
    MembershipNotPending: 409,
    MembershipStoresRequired: 422,
    ForbiddenTeamCapability: 403,
    InvalidPhoneNumber: 422,
    PrinterAuthorizationNotFound: 404,
    SelfPrintingNotAuthorized: 403,
    LabelStockExhausted: 409,
    LabelCountInvalid: 422,
    ProductNotFound: 404,
    ProductNameRequired: 422,
    CategoryNotFound: 404,
    CategoryInUse: 409,
    StaleMerchantRecord: 409,
}

_CODE: dict[type[MerchantError], str] = {
    ApplicationNotFound: "application_not_found",
    ApplicationAlreadyOpen: "application_already_open",
    ApplicationTransitionNotAllowed: "application_transition_not_allowed",
    ApplicationDataSetNotDefined: "merchant_application_data_set_not_defined",
    ApplicationAttributesMissing: "application_attributes_missing",
    DecisionReasonRequired: "decision_reason_required",
    MerchantNotFound: "merchant_not_found",
    MerchantNotActive: "merchant_not_active",
    MerchantCodeAlreadyIssued: "merchant_code_already_issued",
    SenderMayNotSetPlatformPolicy: "sender_may_not_set_platform_policy",
    StoreNotFound: "store_not_found",
    StoreArchived: "store_archived",
    LastStoreCannotBeArchived: "last_store_cannot_be_archived",
    UnknownGovernorate: "unknown_governorate",
    MembershipNotFound: "membership_not_found",
    MembershipAlreadyExists: "membership_already_exists",
    MembershipNotPending: "membership_not_pending",
    MembershipStoresRequired: "membership_stores_required",
    ForbiddenTeamCapability: "forbidden_team_capability",
    InvalidPhoneNumber: "invalid_phone_number",
    PrinterAuthorizationNotFound: "printer_authorization_not_found",
    SelfPrintingNotAuthorized: "self_printing_not_authorized",
    LabelStockExhausted: "label_stock_exhausted",
    LabelCountInvalid: "label_count_invalid",
    ProductNotFound: "product_not_found",
    ProductNameRequired: "product_name_required",
    CategoryNotFound: "category_not_found",
    CategoryInUse: "category_in_use",
    StaleMerchantRecord: "concurrent_modification",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, MerchantError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "merchant_error"),
            "message": str(exc),
        }
        if isinstance(exc, ApplicationAttributesMissing):
            detail["missing"] = list(exc.missing)
        if isinstance(exc, SenderMayNotSetPlatformPolicy):
            detail["keys"] = list(exc.keys)
        if isinstance(exc, ApplicationDataSetNotDefined):
            detail["open_item"] = "MER-02"
            detail["decision_source"] = "v6.3 Appendix A p.44"
        if isinstance(exc, CategoryInUse):
            detail["product_count"] = exc.product_count
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
