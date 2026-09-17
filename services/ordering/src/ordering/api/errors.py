"""Domain error to HTTP mapping for the Ordering service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from ordering.domain.errors import (
    CashOnDeliveryNotAvailableForCustomers,
    CodAmountNotAllowed,
    CodAmountRequired,
    CodAmountTooLateToChange,
    DescriptionRequired,
    FieldNotEditableAtThisStage,
    InvalidLabelCode,
    InvalidPhoneNumber,
    LabelAlreadyLinked,
    LabelNotAllowedForCustomer,
    NotServiceable,
    OrderingError,
    OrderNotFound,
    OrderNotOpen,
    PickupBlockedByMissingLabels,
    PickupNotAvailableForCustomers,
    ProhibitedGoods,
    ProhibitedGoodsNotAcknowledged,
    ReceiverContactIncomplete,
    RequestAlreadyInCustody,
    RequestTransitionNotAllowed,
    SenderMayNotSetPlatformPolicy,
    ShipmentRequestNotFound,
    StaleOrderingRecord,
    TariffNotConfigured,
    UnknownGoodsCategory,
    UnknownGovernorate,
)

_STATUS: dict[type[OrderingError], int] = {
    OrderNotFound: 404,
    ShipmentRequestNotFound: 404,
    OrderNotOpen: 409,
    DescriptionRequired: 422,
    ReceiverContactIncomplete: 422,
    UnknownGovernorate: 422,
    InvalidPhoneNumber: 422,
    UnknownGoodsCategory: 422,
    # 422, not 403: the sender is allowed to send, this content is not accepted.
    ProhibitedGoods: 422,
    ProhibitedGoodsNotAcknowledged: 422,
    # 409, not 422: the request is well formed; the rule is about who is sending.
    CashOnDeliveryNotAvailableForCustomers: 409,
    CodAmountRequired: 422,
    CodAmountNotAllowed: 409,
    CodAmountTooLateToChange: 409,
    PickupNotAvailableForCustomers: 409,
    PickupBlockedByMissingLabels: 409,
    LabelAlreadyLinked: 409,
    LabelNotAllowedForCustomer: 409,
    InvalidLabelCode: 422,
    RequestTransitionNotAllowed: 409,
    RequestAlreadyInCustody: 409,
    FieldNotEditableAtThisStage: 409,
    SenderMayNotSetPlatformPolicy: 403,
    NotServiceable: 422,
    # 503, not 422: the platform is not configured yet; the caller did nothing wrong.
    TariffNotConfigured: 503,
    StaleOrderingRecord: 409,
}

_CODE: dict[type[OrderingError], str] = {
    OrderNotFound: "order_not_found",
    ShipmentRequestNotFound: "shipment_request_not_found",
    OrderNotOpen: "order_not_open",
    DescriptionRequired: "description_required",
    ReceiverContactIncomplete: "receiver_contact_incomplete",
    UnknownGovernorate: "unknown_governorate",
    InvalidPhoneNumber: "invalid_phone_number",
    UnknownGoodsCategory: "unknown_goods_category",
    ProhibitedGoods: "prohibited_goods",
    ProhibitedGoodsNotAcknowledged: "prohibited_goods_not_acknowledged",
    CashOnDeliveryNotAvailableForCustomers: "cod_not_available_for_customers",
    CodAmountRequired: "cod_amount_required",
    CodAmountNotAllowed: "cod_amount_not_allowed",
    CodAmountTooLateToChange: "cod_amount_too_late_to_change",
    PickupNotAvailableForCustomers: "pickup_not_available_for_customers",
    PickupBlockedByMissingLabels: "pickup_blocked_by_missing_labels",
    LabelAlreadyLinked: "label_already_linked",
    LabelNotAllowedForCustomer: "label_not_allowed_for_customer",
    InvalidLabelCode: "invalid_label_code",
    RequestTransitionNotAllowed: "request_transition_not_allowed",
    RequestAlreadyInCustody: "request_already_in_custody",
    FieldNotEditableAtThisStage: "field_not_editable_at_this_stage",
    SenderMayNotSetPlatformPolicy: "sender_may_not_set_platform_policy",
    NotServiceable: "not_serviceable",
    TariffNotConfigured: "tariff_not_configured",
    StaleOrderingRecord: "concurrent_modification",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, OrderingError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "ordering_error"),
            "message": str(exc),
        }
        if isinstance(exc, PickupBlockedByMissingLabels):
            detail["unlabelled"] = exc.unlabelled
        if isinstance(exc, SenderMayNotSetPlatformPolicy):
            detail["keys"] = list(exc.keys)
        if isinstance(exc, FieldNotEditableAtThisStage):
            detail["field"] = exc.field_name
            detail["stage"] = exc.stage
            detail["support_ticket_required"] = True
        if isinstance(exc, ReceiverContactIncomplete):
            detail["missing"] = list(exc.missing)
        if isinstance(exc, TariffNotConfigured):
            detail["origin"] = exc.origin
            detail["destination"] = exc.destination
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
