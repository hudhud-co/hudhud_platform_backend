"""Domain error to HTTP mapping for the Customer service."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from customer.domain.errors import (
    AddressArchived,
    AddressNotFound,
    AddressNotOwnedByCustomer,
    ContactNotFound,
    CustomerError,
    CustomerProfileNotFound,
    DisplayNameRequired,
    LastAddressCannotBeArchived,
    LegalDocumentNotAccepted,
    ReceiverContactIncomplete,
    UnknownGovernorate,
)

_STATUS: dict[type[CustomerError], int] = {
    CustomerProfileNotFound: 404,
    DisplayNameRequired: 422,
    UnknownGovernorate: 422,
    ReceiverContactIncomplete: 422,
    AddressNotFound: 404,
    # Not 403: telling a caller an id exists but is someone else's leaks its existence.
    AddressNotOwnedByCustomer: 404,
    AddressArchived: 409,
    LastAddressCannotBeArchived: 409,
    LegalDocumentNotAccepted: 409,
    ContactNotFound: 404,
}

_CODE: dict[type[CustomerError], str] = {
    CustomerProfileNotFound: "customer_profile_not_found",
    DisplayNameRequired: "display_name_required",
    UnknownGovernorate: "unknown_governorate",
    ReceiverContactIncomplete: "receiver_contact_incomplete",
    AddressNotFound: "address_not_found",
    AddressNotOwnedByCustomer: "address_not_found",
    AddressArchived: "address_archived",
    LastAddressCannotBeArchived: "last_address_cannot_be_archived",
    LegalDocumentNotAccepted: "legal_document_not_accepted",
    ContactNotFound: "contact_not_found",
}


def raise_http_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, CustomerError):
        detail: dict[str, object] = {
            "code": _CODE.get(type(exc), "customer_error"),
            "message": str(exc),
        }
        if isinstance(exc, LegalDocumentNotAccepted):
            detail["required_version"] = exc.required_version
            detail["document"] = exc.kind
        if isinstance(exc, ReceiverContactIncomplete):
            detail["missing"] = list(exc.missing)
        raise HTTPException(status_code=_STATUS.get(type(exc), 400), detail=detail)
    raise exc
