"""Merchant HTTP adapter.

Two rules run through every route here.

First, the actor comes from the authorization decision and never from the request: a body
that names a principal or a merchant is data, not identity.

Second, merchant scope is checked explicitly. Owning a merchant, being a team member of
it, and being Operations are three different things, and a route states which it needs
rather than accepting "signed in" as sufficient.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from merchant.api.errors import raise_http_for_domain_error
from merchant.api.schemas import (
    AllocationResponse,
    ApplicationRequirementsResponse,
    ApplicationResponse,
    AuthorizePrinterRequest,
    CategoryResponse,
    ConsumeLabelsRequest,
    CreateCategoryRequest,
    CreateProductRequest,
    CreateStoreRequest,
    DecideApplicationRequest,
    GeoPointModel,
    InviteMemberRequest,
    IssueStockRequest,
    MembershipResponse,
    MerchantResponse,
    MyMerchantResponse,
    PolicyResponse,
    PrinterAuthorizationResponse,
    ProductResponse,
    RespondToInviteRequest,
    StartApplicationRequest,
    StockSummaryResponse,
    StoreAccessResponse,
    StoreResponse,
    UpdateApplicationRequest,
    UpdateBranchesRequest,
    UpdatePolicyRequest,
    UpdateProductRequest,
    UpdateStoreRequest,
)
from merchant.application.application_service import MerchantApplicationService
from merchant.application.catalogue_service import CatalogueService
from merchant.application.label_stock_service import LabelStockService, StockSummary
from merchant.application.store_service import (
    PLATFORM_FIXED_POLICY,
    StoreDraft,
    StoreService,
)
from merchant.application.team_service import InviteDraft, TeamService, phone_last4
from merchant.domain.entities import (
    LabelStockAllocation,
    Merchant,
    MerchantApplication,
    PrinterAuthorization,
    Product,
    ProductCategory,
    StandingShipmentPolicy,
    Store,
    StoreAccess,
    TeamMembership,
)
from merchant.domain.errors import MerchantError, MerchantNotFound
from merchant.domain.value_objects import (
    DeliveryFeePayer,
    GeoPoint,
    LabelStockSource,
    StockKind,
)
from merchant.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    MerchantActor,
    MerchantAuthorizer,
    MerchantCommand,
)

router = APIRouter(prefix="/merchant", tags=["merchant"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_applications(request: Request) -> MerchantApplicationService:
    return _state(request, "application_service", "application")


def get_stores(request: Request) -> StoreService:
    return _state(request, "store_service", "store")


def get_team(request: Request) -> TeamService:
    return _state(request, "team_service", "team")


def get_labels(request: Request) -> LabelStockService:
    return _state(request, "label_stock_service", "label_stock")


def get_catalogue(request: Request) -> CatalogueService:
    return _state(request, "catalogue_service", "catalogue")


def get_authorizer(request: Request) -> MerchantAuthorizer:
    return _state(request, "authorizer", "authorizer")


def get_unit_of_work(request: Request):
    return _state(request, "unit_of_work", "persistence")


Applications = Annotated[MerchantApplicationService, Depends(get_applications)]
Stores = Annotated[StoreService, Depends(get_stores)]
Team = Annotated[TeamService, Depends(get_team)]
Labels = Annotated[LabelStockService, Depends(get_labels)]
Catalogue = Annotated[CatalogueService, Depends(get_catalogue)]
Authorizer = Annotated[MerchantAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: MerchantAuthorizer, header: str | None, command: MerchantCommand
) -> MerchantActor:
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
    # Order matters: a forbidden decision reported as unauthenticated would tell a
    # signed-in caller to sign in again.
    if decision.outcome is AuthorizationOutcome.FORBIDDEN:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    if not decision.allowed or decision.actor is None:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated"})
    return decision.actor


def _require_merchant_scope(actor: MerchantActor, merchant_id: UUID, request: Request) -> None:
    """Owning, working at, or administering a merchant — one of the three, explicitly.

    Ownership is read from this service's own data rather than trusted from the token, so
    a stale role claim cannot grant write access to a merchant that changed hands.
    """
    if actor.is_operations:
        return
    uow = getattr(request.app.state, "unit_of_work", None)
    if uow is None:
        raise HTTPException(status_code=503, detail={"code": "persistence_unavailable"})
    uow.begin()
    try:
        merchant = uow.merchants.get(merchant_id)
        memberships = uow.memberships.list_for_principal(actor.principal_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if merchant is None:
        raise HTTPException(status_code=404, detail={"code": "merchant_not_found"})
    if merchant.owner_principal_id == actor.principal_id:
        return
    if any(
        membership.merchant_id == merchant_id and membership.is_active
        for membership in memberships
    ):
        return
    raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _require_owner_or_operations(
    actor: MerchantActor, merchant_id: UUID, request: Request
) -> None:
    """Writes to a merchant's own configuration are the owner's, never a keeper's.

    A warehouse keeper prepares parcels; they do not open branches, invite colleagues,
    change the standing policy or order label stock.
    """
    if actor.is_operations:
        return
    uow = getattr(request.app.state, "unit_of_work", None)
    if uow is None:
        raise HTTPException(status_code=503, detail={"code": "persistence_unavailable"})
    uow.begin()
    try:
        merchant = uow.merchants.get(merchant_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if merchant is None:
        raise HTTPException(status_code=404, detail={"code": "merchant_not_found"})
    if merchant.owner_principal_id != actor.principal_id:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


# ------------------------------------------------------------------ application


@router.get("/applications/requirements", response_model=ApplicationRequirementsResponse)
async def application_requirements(
    request: Request,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationRequirementsResponse:
    """Tell the app whether the application flow can be used at all (MER-02)."""
    await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_READ,
    )
    settings = request.app.state.settings
    enabled = settings.application_submission_enabled
    return ApplicationRequirementsResponse(
        submission_enabled=enabled,
        required_attributes=list(settings.application_required_attributes),
        blocked_reason=(
            None
            if enabled
            else (
                "The merchant-application data set is an unresolved v6.3 open item "
                "(Appendix A p.44). Applications can be drafted but not submitted."
            )
        ),
    )


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
async def start_application(
    payload: StartApplicationRequest,
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_CREATE,
    )
    try:
        application = applications.start_application(
            applicant_principal_id=actor.principal_id, attributes=payload.attributes
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


@router.get("/applications", response_model=list[ApplicationResponse])
async def list_applications(
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ApplicationResponse]:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_READ,
    )
    found = applications.list_for_applicant(actor.principal_id)
    return [_application_response(application) for application in found]


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: UUID,
    payload: UpdateApplicationRequest,
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_UPDATE,
    )
    _require_own_application(applications, application_id, actor)
    try:
        application = applications.update_attributes(
            application_id=application_id, attributes=payload.attributes
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


@router.post("/applications/{application_id}/submit", response_model=ApplicationResponse)
async def submit_application(
    application_id: UUID,
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_SUBMIT,
    )
    _require_own_application(applications, application_id, actor)
    try:
        application = applications.submit(application_id=application_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


@router.post("/applications/{application_id}/withdraw", response_model=ApplicationResponse)
async def withdraw_application(
    application_id: UUID,
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_WITHDRAW,
    )
    _require_own_application(applications, application_id, actor)
    try:
        application = applications.withdraw(application_id=application_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


@router.post("/applications/{application_id}/decision", response_model=ApplicationResponse)
async def decide_application(
    application_id: UUID,
    payload: DecideApplicationRequest,
    applications: Applications,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ApplicationResponse:
    """Review outcome. Operations only — an applicant can never decide their own case."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.APPLICATION_DECIDE,
    )
    if not actor.can_review_applications:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        if payload.decision == "APPROVED":
            if not (payload.display_name or "").strip():
                raise HTTPException(
                    status_code=422, detail={"code": "display_name_required"}
                )
            result = applications.approve(
                application_id=application_id,
                reviewer_principal_id=actor.principal_id,
                display_name=payload.display_name or "",
                merchant_code=payload.merchant_code,
            )
            return _application_response(result.application)
        application = applications.request_changes(
            application_id=application_id,
            reviewer_principal_id=actor.principal_id,
            reason=payload.reason or "",
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _application_response(application)


def _require_own_application(
    applications: MerchantApplicationService, application_id: UUID, actor: MerchantActor
) -> None:
    try:
        application = applications.get(application_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    if application.applicant_principal_id != actor.principal_id:
        # 404 rather than 403: confirming the id exists would leak someone else's case.
        raise HTTPException(status_code=404, detail={"code": "application_not_found"})


# ------------------------------------------------------------------ merchant


@router.get("/merchants/{merchant_id}", response_model=MerchantResponse)
async def read_merchant(
    merchant_id: UUID,
    request: Request,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MerchantResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.MERCHANT_READ,
    )
    _require_merchant_scope(actor, merchant_id, request)
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        merchant = uow.merchants.get(merchant_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if merchant is None:
        raise_http_for_domain_error(MerchantNotFound(str(merchant_id)))
    return _merchant_response(merchant)


@router.get("/merchants/{merchant_id}/policy", response_model=PolicyResponse)
async def read_policy(
    merchant_id: UUID,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PolicyResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.POLICY_READ
    )
    _require_merchant_scope(actor, merchant_id, request)
    try:
        policy = stores.get_policy(merchant_id=merchant_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _policy_response(policy)


@router.patch("/merchants/{merchant_id}/policy", response_model=PolicyResponse)
async def update_policy(
    merchant_id: UUID,
    payload: UpdatePolicyRequest,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PolicyResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.POLICY_UPDATE,
    )
    _require_owner_or_operations(actor, merchant_id, request)
    try:
        policy = stores.update_policy(
            merchant_id=merchant_id,
            open_box_allowed=payload.open_box_allowed,
            photo_documentation_enabled=payload.photo_documentation_enabled,
            hudhud_packaging_enabled=payload.hudhud_packaging_enabled,
            delivery_fee_payer=(
                DeliveryFeePayer(payload.delivery_fee_payer)
                if payload.delivery_fee_payer
                else None
            ),
            rejected_keys=payload.extra_keys(),
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _policy_response(policy)


# ------------------------------------------------------------------ stores


@router.post("/merchants/{merchant_id}/stores", response_model=StoreResponse, status_code=201)
async def create_store(
    merchant_id: UUID,
    payload: CreateStoreRequest,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StoreResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.STORE_CREATE
    )
    _require_owner_or_operations(actor, merchant_id, request)
    try:
        store = stores.create_store(
            merchant_id=merchant_id,
            draft=StoreDraft(
                name=payload.name,
                governorate=payload.governorate,
                address_line=payload.address_line,
                area=payload.area,
                landmark=payload.landmark,
                geo=_geo(payload.geo),
                make_default_pickup=payload.make_default_pickup,
            ),
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _store_response(store)


@router.get("/merchants/{merchant_id}/stores", response_model=list[StoreResponse])
async def list_stores(
    merchant_id: UUID,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    include_archived: bool = False,
    authorization: BearerHeader = None,
) -> list[StoreResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.STORE_READ
    )
    _require_merchant_scope(actor, merchant_id, request)
    found = stores.list_stores(
        merchant_id=merchant_id, include_archived=include_archived
    )
    return [_store_response(store) for store in found]


@router.patch("/stores/{store_id}", response_model=StoreResponse)
async def update_store(
    store_id: UUID,
    payload: UpdateStoreRequest,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StoreResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.STORE_UPDATE
    )
    _require_owner_or_operations(actor, _merchant_of_store(request, store_id), request)
    try:
        store = stores.update_store(
            store_id=store_id,
            name=payload.name,
            governorate=payload.governorate,
            address_line=payload.address_line,
            area=payload.area,
            landmark=payload.landmark,
            geo=_geo(payload.geo),
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _store_response(store)


@router.post("/stores/{store_id}/default-pickup", response_model=StoreResponse)
async def set_default_pickup(
    store_id: UUID,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StoreResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.STORE_UPDATE
    )
    _require_owner_or_operations(actor, _merchant_of_store(request, store_id), request)
    try:
        store = stores.set_default_pickup(store_id=store_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _store_response(store)


@router.post("/stores/{store_id}/archive", response_model=StoreResponse)
async def archive_store(
    store_id: UUID,
    request: Request,
    stores: Stores,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StoreResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.STORE_ARCHIVE
    )
    _require_owner_or_operations(actor, _merchant_of_store(request, store_id), request)
    try:
        store = stores.archive_store(store_id=store_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _store_response(store)


def _merchant_of_store(request: Request, store_id: UUID) -> UUID:
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        store = uow.stores.get(store_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if store is None:
        raise HTTPException(status_code=404, detail={"code": "store_not_found"})
    return store.merchant_id


# ------------------------------------------------------------------ team


@router.post(
    "/merchants/{merchant_id}/team", response_model=MembershipResponse, status_code=201
)
async def invite_member(
    merchant_id: UUID,
    payload: InviteMemberRequest,
    request: Request,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MembershipResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.TEAM_INVITE
    )
    _require_owner_or_operations(actor, merchant_id, request)
    try:
        membership = team.invite(
            merchant_id=merchant_id,
            draft=InviteDraft(
                phone=payload.phone,
                store_ids=tuple(payload.store_ids),
                display_name=payload.display_name,
            ),
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _membership_response(membership)


@router.get("/merchants/{merchant_id}/team", response_model=list[MembershipResponse])
async def list_team(
    merchant_id: UUID,
    request: Request,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[MembershipResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.TEAM_READ
    )
    _require_owner_or_operations(actor, merchant_id, request)
    return [
        _membership_response(membership)
        for membership in team.list_for_merchant(merchant_id=merchant_id)
    ]


@router.patch("/team/{membership_id}/branches", response_model=MembershipResponse)
async def update_branches(
    membership_id: UUID,
    payload: UpdateBranchesRequest,
    request: Request,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MembershipResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.TEAM_UPDATE
    )
    _require_owner_or_operations(actor, _merchant_of_membership(request, membership_id), request)
    try:
        membership = team.update_branches(
            membership_id=membership_id, store_ids=tuple(payload.store_ids)
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _membership_response(membership)


@router.post("/team/{membership_id}/respond", response_model=MembershipResponse)
async def respond_to_invite(
    membership_id: UUID,
    payload: RespondToInviteRequest,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MembershipResponse:
    """The invitee answers in their own account — the only route a non-owner may call."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.TEAM_RESPOND
    )
    try:
        if payload.accept:
            membership = team.accept(
                membership_id=membership_id, member_principal_id=actor.principal_id
            )
        else:
            membership = team.decline(
                membership_id=membership_id, member_principal_id=actor.principal_id
            )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _membership_response(membership)


@router.post("/team/{membership_id}/remove", response_model=MembershipResponse)
async def remove_member(
    membership_id: UUID,
    request: Request,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MembershipResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=MerchantCommand.TEAM_REMOVE
    )
    _require_owner_or_operations(actor, _merchant_of_membership(request, membership_id), request)
    try:
        membership = team.remove(membership_id=membership_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _membership_response(membership)


@router.get("/me/merchants", response_model=list[MyMerchantResponse])
async def list_my_merchants(
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[MyMerchantResponse]:
    """Which stores the signed-in principal may act for, and in what capacity.

    Backs Customer App v3 `home` → `yourStores` and the store picker. It answers the one
    question neither existing read could: ``store-access`` reports team memberships only,
    so an owner saw nothing of their own store, and an approved application never revealed
    the merchant it created.

    Identity comes from the session. There is no principal parameter, so no caller can ask
    which stores somebody else belongs to.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.STORE_ACCESS_QUERY,
    )
    return [
        MyMerchantResponse(
            merchant_id=affiliation.merchant.merchant_id,
            merchant_code=affiliation.merchant.merchant_code,
            display_name=affiliation.merchant.display_name,
            status=affiliation.merchant.status.value,
            relationship=affiliation.relationship,
            role=affiliation.role,
            permissions=sorted(affiliation.permissions),
            store_ids=list(affiliation.store_ids),
        )
        for affiliation in team.my_merchants(principal_id=actor.principal_id)
    ]


@router.get("/principals/{principal_id}/store-access", response_model=list[StoreAccessResponse])
async def read_store_access(
    principal_id: UUID,
    team: Team,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[StoreAccessResponse]:
    """The read model Ordering and Finance consume instead of reading Merchant's tables.

    A principal may read their own access; anyone else needs Operations or Support.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.STORE_ACCESS_QUERY,
    )
    if actor.principal_id != principal_id and not (actor.is_operations or actor.is_support):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return [_access_response(access) for access in team.store_access(principal_id=principal_id)]


def _merchant_of_membership(request: Request, membership_id: UUID) -> UUID:
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        membership = uow.memberships.get(membership_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if membership is None:
        raise HTTPException(status_code=404, detail={"code": "membership_not_found"})
    return membership.merchant_id


# ------------------------------------------------------------------ labels


@router.post(
    "/merchants/{merchant_id}/label-stock",
    response_model=AllocationResponse,
    status_code=201,
)
async def issue_stock(
    merchant_id: UUID,
    payload: IssueStockRequest,
    labels: Labels,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AllocationResponse:
    """Issue pre-printed stock. Operations only — a merchant cannot mint their own."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.LABEL_STOCK_ISSUE,
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        allocation = labels.issue_stock(
            merchant_id=merchant_id,
            batch_reference=payload.batch_reference,
            label_count=payload.label_count,
            source=LabelStockSource(payload.source),
            printer_authorization_id=payload.printer_authorization_id,
            stock_kind=StockKind(payload.stock_kind),
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _allocation_response(allocation)


@router.post("/label-stock/{allocation_id}/consume", response_model=AllocationResponse)
async def consume_labels(
    allocation_id: UUID,
    payload: ConsumeLabelsRequest,
    request: Request,
    labels: Labels,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AllocationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.LABEL_STOCK_ISSUE,
    )
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        allocation = uow.label_stock.get(allocation_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if allocation is None:
        raise HTTPException(status_code=404, detail={"code": "label_stock_not_found"})
    _require_merchant_scope(actor, allocation.merchant_id, request)
    try:
        updated = labels.consume_labels(
            allocation_id=allocation_id, count=payload.count
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _allocation_response(updated)


@router.get("/merchants/{merchant_id}/label-stock", response_model=StockSummaryResponse)
async def read_stock_summary(
    merchant_id: UUID,
    request: Request,
    labels: Labels,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StockSummaryResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.LABEL_STOCK_READ,
    )
    _require_merchant_scope(actor, merchant_id, request)
    return _summary_response(labels.summarize(merchant_id=merchant_id))


@router.post(
    "/merchants/{merchant_id}/printer-authorizations",
    response_model=PrinterAuthorizationResponse,
    status_code=201,
)
async def authorize_printer(
    merchant_id: UUID,
    payload: AuthorizePrinterRequest,
    labels: Labels,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PrinterAuthorizationResponse:
    """Record Hudhud-supplied printing equipment. Operations only (v6.3 p.12)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.PRINTER_AUTHORIZE,
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        authorization_record = labels.authorize_printer(
            merchant_id=merchant_id,
            printer_serial=payload.printer_serial,
            stock_reference=payload.stock_reference,
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _printer_response(authorization_record)


@router.post(
    "/printer-authorizations/{authorization_id}/revoke",
    response_model=PrinterAuthorizationResponse,
)
async def revoke_printer(
    authorization_id: UUID,
    labels: Labels,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PrinterAuthorizationResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.PRINTER_REVOKE,
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        record = labels.revoke_printer(authorization_id=authorization_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _printer_response(record)


# ------------------------------------------------------------------ catalogue


@router.post(
    "/merchants/{merchant_id}/categories", response_model=CategoryResponse, status_code=201
)
async def create_category(
    merchant_id: UUID,
    payload: CreateCategoryRequest,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CategoryResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_WRITE,
    )
    _require_owner_or_operations(actor, merchant_id, request)
    try:
        category = catalogue.create_category(merchant_id=merchant_id, name=payload.name)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _category_response(category)


@router.get("/merchants/{merchant_id}/categories", response_model=list[CategoryResponse])
async def list_categories(
    merchant_id: UUID,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[CategoryResponse]:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_READ,
    )
    _require_merchant_scope(actor, merchant_id, request)
    return [
        _category_response(category)
        for category in catalogue.list_categories(merchant_id=merchant_id)
    ]


@router.delete("/categories/{category_id}", response_model=CategoryResponse)
async def delete_category(
    category_id: UUID,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CategoryResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_WRITE,
    )
    _require_owner_or_operations(
        actor, _merchant_of_category(request, category_id), request
    )
    try:
        category = catalogue.delete_category(category_id=category_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _category_response(category)


@router.post(
    "/merchants/{merchant_id}/products", response_model=ProductResponse, status_code=201
)
async def create_product(
    merchant_id: UUID,
    payload: CreateProductRequest,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProductResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_WRITE,
    )
    _require_owner_or_operations(actor, merchant_id, request)
    try:
        product = catalogue.create_product(
            merchant_id=merchant_id,
            name=payload.name,
            category_id=payload.category_id,
            description=payload.description,
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _product_response(product)


@router.get("/merchants/{merchant_id}/products", response_model=list[ProductResponse])
async def list_products(
    merchant_id: UUID,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    category_id: UUID | None = None,
    authorization: BearerHeader = None,
) -> list[ProductResponse]:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_READ,
    )
    _require_merchant_scope(actor, merchant_id, request)
    return [
        _product_response(product)
        for product in catalogue.list_products(
            merchant_id=merchant_id, category_id=category_id
        )
    ]


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    payload: UpdateProductRequest,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProductResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_WRITE,
    )
    _require_owner_or_operations(actor, _merchant_of_product(request, product_id), request)
    try:
        product = catalogue.update_product(
            product_id=product_id,
            name=payload.name,
            category_id=payload.category_id,
            description=payload.description,
        )
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _product_response(product)


@router.delete("/products/{product_id}", response_model=ProductResponse)
async def archive_product(
    product_id: UUID,
    request: Request,
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProductResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=MerchantCommand.CATALOGUE_WRITE,
    )
    _require_owner_or_operations(actor, _merchant_of_product(request, product_id), request)
    try:
        product = catalogue.archive_product(product_id=product_id)
    except MerchantError as exc:
        raise_http_for_domain_error(exc)
    return _product_response(product)


def _merchant_of_category(request: Request, category_id: UUID) -> UUID:
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        category = uow.catalogue.get_category(category_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if category is None:
        raise HTTPException(status_code=404, detail={"code": "category_not_found"})
    return category.merchant_id


def _merchant_of_product(request: Request, product_id: UUID) -> UUID:
    uow = get_unit_of_work(request)
    uow.begin()
    try:
        product = uow.catalogue.get_product(product_id)
        uow.commit()
    except Exception:
        uow.rollback()
        raise
    if product is None:
        raise HTTPException(status_code=404, detail={"code": "product_not_found"})
    return product.merchant_id


# ------------------------------------------------------------------ responses


def _geo(model: GeoPointModel | None) -> GeoPoint | None:
    if model is None:
        return None
    return GeoPoint(latitude=model.latitude, longitude=model.longitude)


def _application_response(application: MerchantApplication) -> ApplicationResponse:
    return ApplicationResponse(
        application_id=application.application_id,
        reference=application.reference,
        status=application.status.value,
        attributes=application.attributes,
        submitted_at=application.submitted_at,
        decided_at=application.decided_at,
        decision_reason=application.decision_reason,
        version=application.version,
    )


def _merchant_response(merchant: Merchant) -> MerchantResponse:
    return MerchantResponse(
        merchant_id=merchant.merchant_id,
        merchant_code=merchant.merchant_code,
        display_name=merchant.display_name,
        status=merchant.status.value,
        activated_at=merchant.activated_at,
        version=merchant.version,
    )


def _policy_response(policy: StandingShipmentPolicy) -> PolicyResponse:
    return PolicyResponse(
        merchant_id=policy.merchant_id,
        open_box_allowed=policy.open_box_allowed,
        photo_documentation_enabled=policy.photo_documentation_enabled,
        hudhud_packaging_enabled=policy.hudhud_packaging_enabled,
        delivery_fee_payer=policy.delivery_fee_payer.value,
        platform_fixed=dict(PLATFORM_FIXED_POLICY),
        version=policy.version,
    )


def _store_response(store: Store) -> StoreResponse:
    return StoreResponse(
        store_id=store.store_id,
        merchant_id=store.merchant_id,
        name=store.name,
        governorate=store.governorate,
        address_line=store.address_line,
        area=store.area,
        landmark=store.landmark,
        geo=(
            GeoPointModel(latitude=store.geo.latitude, longitude=store.geo.longitude)
            if store.geo
            else None
        ),
        is_default_pickup=store.is_default_pickup,
        archived_at=store.archived_at,
        version=store.version,
    )


def _membership_response(membership: TeamMembership) -> MembershipResponse:
    return MembershipResponse(
        membership_id=membership.membership_id,
        merchant_id=membership.merchant_id,
        phone_last4=phone_last4(membership.invited_phone),
        display_name=membership.display_name,
        role=membership.role.value,
        status=membership.status.value,
        store_ids=list(membership.store_ids),
        permissions=sorted(
            permission.value for permission in membership.permissions
        ),
        invited_at=membership.invited_at,
        accepted_at=membership.accepted_at,
        version=membership.version,
    )


def _access_response(access: StoreAccess) -> StoreAccessResponse:
    return StoreAccessResponse(
        merchant_id=access.merchant_id,
        principal_id=access.principal_id,
        role=access.role.value,
        permissions=sorted(permission.value for permission in access.permissions),
        store_ids=list(access.store_ids),
    )


def _allocation_response(allocation: LabelStockAllocation) -> AllocationResponse:
    return AllocationResponse(
        allocation_id=allocation.allocation_id,
        batch_reference=allocation.batch_reference,
        source=allocation.source.value,
        stock_kind=allocation.stock_kind.value,
        label_count=allocation.label_count,
        consumed_count=allocation.consumed_count,
        remaining=allocation.remaining,
        printer_authorization_id=allocation.printer_authorization_id,
        version=allocation.version,
    )


def _summary_response(summary: StockSummary) -> StockSummaryResponse:
    return StockSummaryResponse(
        merchant_id=summary.merchant_id,
        total_labels=summary.total_labels,
        consumed_labels=summary.consumed_labels,
        remaining=summary.remaining,
        total_seals=summary.total_seals,
        consumed_seals=summary.consumed_seals,
        remaining_seals=summary.remaining_seals,
    )


def _printer_response(record: PrinterAuthorization) -> PrinterAuthorizationResponse:
    return PrinterAuthorizationResponse(
        authorization_id=record.authorization_id,
        merchant_id=record.merchant_id,
        printer_serial=record.printer_serial,
        stock_reference=record.stock_reference,
        status=record.status.value,
        version=record.version,
    )


def _category_response(category: ProductCategory) -> CategoryResponse:
    return CategoryResponse(
        category_id=category.category_id,
        merchant_id=category.merchant_id,
        name=category.name,
        version=category.version,
    )


def _product_response(product: Product) -> ProductResponse:
    return ProductResponse(
        product_id=product.product_id,
        merchant_id=product.merchant_id,
        name=product.name,
        category_id=product.category_id,
        description=product.description,
        version=product.version,
    )
