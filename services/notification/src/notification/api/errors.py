"""Domain error to HTTP mapping for the Notification service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from notification.domain.errors import (
    CentreEntryNotFound,
    DeliveryCodeMustNotBeStored,
    DeliveryTransitionNotAllowed,
    InvalidPhoneNumber,
    MandatoryNotificationCannotBeDisabled,
    NotificationError,
    NotificationNotFound,
    StaleNotificationRecord,
    TemplateNotFound,
    TransportUnavailable,
)

_STATUS: dict[type[NotificationError], int] = {
    NotificationNotFound: 404,
    CentreEntryNotFound: 404,
    DeliveryTransitionNotAllowed: 409,
    # 409, not 422: the request is well formed; this switch simply does not move.
    MandatoryNotificationCannotBeDisabled: 409,
    InvalidPhoneNumber: 422,
    TemplateNotFound: 500,
    # A transport outage is a platform fault, never the caller's.
    TransportUnavailable: 503,
    # A 500 on purpose: this is a bug in this service, not bad input.
    DeliveryCodeMustNotBeStored: 500,
    StaleNotificationRecord: 409,
}

_CODE: dict[type[NotificationError], str] = {
    NotificationNotFound: "notification_not_found",
    CentreEntryNotFound: "centre_entry_not_found",
    DeliveryTransitionNotAllowed: "delivery_transition_not_allowed",
    MandatoryNotificationCannotBeDisabled: "notification_cannot_be_disabled",
    InvalidPhoneNumber: "invalid_phone_number",
    TemplateNotFound: "template_not_found",
    TransportUnavailable: "transport_unavailable",
    DeliveryCodeMustNotBeStored: "delivery_code_must_not_be_stored",
    StaleNotificationRecord: "concurrent_modification",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, NotificationError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "notification_error"),
            "message": str(exc),
        }
        if isinstance(exc, MandatoryNotificationCannotBeDisabled):
            detail["category"] = exc.category
            detail["channel"] = exc.channel
            detail["reason"] = "it carries the delivery code (v6.3 p.20)"
        if isinstance(exc, DeliveryCodeMustNotBeStored):
            # Deliberately does not echo the offending value.
            detail["field"] = exc.field_name
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
