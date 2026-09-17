"""Customer HTTP adapter.

Every route resolves the caller through the authorizer and then acts *only* on that
caller's own data: the principal id comes from the authorization decision, never from the
path or the body. Support and Operations may read another customer, but never write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from customer.api.errors import raise_http_for_domain_error
from customer.api.schemas import (
    AcceptLegalRequest,
    AddressResponse,
    ContactResponse,
    CreateAddressRequest,
    CreateContactRequest,
    GeoPointModel,
    NotificationPreferencesRequest,
    OutstandingDocument,
    ProfileResponse,
    SetDisplayNameRequest,
)
from customer.application.address_book_service import (
    AddressBookService,
    CreateAddressCommand,
    CreateContactCommand,
)
from customer.application.profile_service import (
    AcceptLegalCommand,
    CustomerProfileService,
    ProfileView,
    UpsertProfileCommand,
)
from customer.domain.entities import Address, Contact
from customer.domain.value_objects import AddressKind, GeoPoint, NotificationChannel
from customer.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    CustomerActor,
    CustomerAuthorizer,
    CustomerCommand,
)

router = APIRouter(prefix="/customer", tags=["customer"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _service(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_profiles(request: Request) -> CustomerProfileService:
    return _service(request, "profile_service", "profile")


def get_addresses(request: Request) -> AddressBookService:
    return _service(request, "address_book_service", "address_book")


def get_authorizer(request: Request) -> CustomerAuthorizer:
    return _service(request, "authorizer", "authorizer")


Profiles = Annotated[CustomerProfileService, Depends(get_profiles)]
Addresses = Annotated[AddressBookService, Depends(get_addresses)]
Authorizer = Annotated[CustomerAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: CustomerAuthorizer, header: str | None, command: CustomerCommand
) -> CustomerActor:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_bearer_token"})
    token = header.split(" ", 1)[1].strip()
    try:
        decision = await authorizer.authorize(bearer_token=token, command=command)
    except AuthorizerUnavailableError:
        # An authorization outage is a platform fault, not a user denial.
        raise HTTPException(
            status_code=503, detail={"code": "authorization_unavailable"}
        ) from None
    # Order matters: a forbidden decision must not be reported as unauthenticated,
    # or a caller who is signed in but lacks the right would be told to sign in again.
    if decision.outcome is AuthorizationOutcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    if not decision.allowed or decision.actor is None:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated"})
    return decision.actor


def _profile_response(view: ProfileView) -> ProfileResponse:
    return ProfileResponse(
        principal_id=view.profile.principal_id,
        display_name=view.profile.display_name,
        completion_state=view.completion_state.value,
        notification_channels=sorted(
            channel.value for channel in view.profile.notification_channels
        ),
        outstanding_documents=[
            OutstandingDocument(kind=kind.value, required_version=version)
            for kind, version in view.outstanding_documents
        ],
        can_use_the_app=view.can_use_the_app,
        version=view.profile.version,
        idempotent_replay=view.idempotent_replay,
    )


def _contact_response(contact: Contact) -> ContactResponse:
    return ContactResponse(
        contact_id=contact.contact_id,
        phone=contact.phone,
        governorate=contact.governorate,
        display_name=contact.display_name,
        archived_at=contact.archived_at,
    )


def _address_response(address: Address) -> AddressResponse:
    return AddressResponse(
        address_id=address.address_id,
        kind=address.kind.value,
        governorate=address.governorate,
        line=address.line,
        contact_id=address.contact_id,
        landmark=address.landmark,
        geo=(
            GeoPointModel(latitude=address.geo.latitude, longitude=address.geo.longitude)
            if address.geo
            else None
        ),
        is_default=address.is_default,
        archived_at=address.archived_at,
    )


# ------------------------------------------------------------------ profile


@router.get("/me", response_model=ProfileResponse)
async def read_my_profile(
    service: Profiles, authorizer: Authorizer, authorization: BearerHeader = None
) -> ProfileResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=CustomerCommand.PROFILE_READ
    )
    try:
        view = service.ensure_profile(
            principal_id=actor.principal_id, occurred_at=_now()
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _profile_response(view)


@router.post("/me/display-name", response_model=ProfileResponse)
async def set_display_name(
    body: SetDisplayNameRequest,
    service: Profiles,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProfileResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.PROFILE_UPDATE,
    )
    try:
        view = service.set_display_name(
            UpsertProfileCommand(
                principal_id=actor.principal_id,
                display_name=body.display_name,
                occurred_at=_now(),
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _profile_response(view)


@router.post("/me/legal-acceptances", response_model=ProfileResponse)
async def accept_legal_document(
    body: AcceptLegalRequest,
    service: Profiles,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProfileResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=CustomerCommand.LEGAL_ACCEPT
    )
    try:
        view = service.accept_legal_document(
            AcceptLegalCommand(
                principal_id=actor.principal_id,
                kind=body.kind,
                document_version=body.document_version,
                occurred_at=_now(),
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _profile_response(view)


@router.post("/me/notification-preferences", response_model=ProfileResponse)
async def set_notification_preferences(
    body: NotificationPreferencesRequest,
    service: Profiles,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProfileResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.PROFILE_UPDATE,
    )
    try:
        channels = frozenset(
            NotificationChannel(value.strip().upper()) for value in body.channels
        )
    except ValueError:
        raise HTTPException(
            status_code=422, detail={"code": "unknown_notification_channel"}
        ) from None
    try:
        view = service.set_notification_channels(
            principal_id=actor.principal_id, channels=channels, occurred_at=_now()
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _profile_response(view)


# ----------------------------------------------------------------- contacts


@router.post("/me/contacts", response_model=ContactResponse, status_code=201)
async def create_contact(
    body: CreateContactRequest,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ContactResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.CONTACT_CREATE,
    )
    try:
        contact = service.create_contact(
            CreateContactCommand(
                owner_principal_id=actor.principal_id,
                phone=body.phone,
                governorate=body.governorate,
                occurred_at=_now(),
                display_name=body.display_name,
            )
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _contact_response(contact)


@router.get("/me/contacts", response_model=list[ContactResponse])
async def list_contacts(
    service: Addresses, authorizer: Authorizer, authorization: BearerHeader = None
) -> list[ContactResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=CustomerCommand.CONTACT_READ
    )
    return [_contact_response(c) for c in service.list_contacts(actor.principal_id)]


@router.post("/me/contacts/{contact_id}/archive", response_model=ContactResponse)
async def archive_contact(
    contact_id: UUID,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ContactResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.CONTACT_ARCHIVE,
    )
    try:
        contact = service.archive_contact(
            contact_id=contact_id,
            owner_principal_id=actor.principal_id,
            occurred_at=_now(),
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _contact_response(contact)


# ---------------------------------------------------------------- addresses


@router.post("/me/addresses", response_model=AddressResponse, status_code=201)
async def create_address(
    body: CreateAddressRequest,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AddressResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.ADDRESS_CREATE,
    )
    try:
        address = service.create_address(
            CreateAddressCommand(
                owner_principal_id=actor.principal_id,
                kind=body.kind,
                governorate=body.governorate,
                line=body.line,
                occurred_at=_now(),
                contact_id=body.contact_id,
                landmark=body.landmark,
                geo=(
                    GeoPoint(latitude=body.geo.latitude, longitude=body.geo.longitude)
                    if body.geo
                    else None
                ),
                make_default=body.make_default,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_geo_point", "message": str(exc)}
        ) from exc
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _address_response(address)


@router.get("/me/addresses", response_model=list[AddressResponse])
async def list_addresses(
    service: Addresses,
    authorizer: Authorizer,
    kind: str | None = None,
    include_archived: bool = False,
    authorization: BearerHeader = None,
) -> list[AddressResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=CustomerCommand.ADDRESS_READ
    )
    parsed = None
    if kind is not None:
        try:
            parsed = AddressKind(kind.strip().upper())
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "unknown_address_kind"}
            ) from None
    addresses = service.list_addresses(
        actor.principal_id, kind=parsed, include_archived=include_archived
    )
    return [_address_response(a) for a in addresses]


@router.post("/me/addresses/{address_id}/default", response_model=AddressResponse)
async def set_default_address(
    address_id: UUID,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AddressResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.ADDRESS_SET_DEFAULT,
    )
    try:
        address = service.set_default(
            address_id=address_id,
            owner_principal_id=actor.principal_id,
            occurred_at=_now(),
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _address_response(address)


@router.post("/me/addresses/{address_id}/archive", response_model=AddressResponse)
async def archive_address(
    address_id: UUID,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AddressResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.ADDRESS_ARCHIVE,
    )
    try:
        address = service.archive_address(
            address_id=address_id,
            owner_principal_id=actor.principal_id,
            occurred_at=_now(),
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _address_response(address)


@router.post("/me/addresses/{address_id}/restore", response_model=AddressResponse)
async def restore_address(
    address_id: UUID,
    service: Addresses,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AddressResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=CustomerCommand.ADDRESS_RESTORE,
    )
    try:
        address = service.restore_address(
            address_id=address_id,
            owner_principal_id=actor.principal_id,
            occurred_at=_now(),
        )
    except Exception as exc:
        raise_http_for_domain_error(exc)
    return _address_response(address)
