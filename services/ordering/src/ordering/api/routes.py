"""Ordering HTTP adapter.

Who a caller is comes from Identity. What they may do inside a merchant comes from
Merchant. Ordering combines the two: a merchant order is only accepted from a principal
Merchant confirms owns that merchant, and a warehouse keeper — who "cannot create, edit or
cancel a shipment" — is refused here, not merely omitted from the app's menu.

The public tracking route is the one exception: it takes no token at all, and returns a
deliberately thin view.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ordering.api.errors import raise_http_for_domain_error
from ordering.api.schemas import (
    AddOnsModel,
    AmendmentResponse,
    AmendShipmentRequest,
    BookPickupRequest,
    BulkShipmentRequest,
    CancelShipmentRequest,
    ChangeCodRequest,
    GoodsCategoryResponse,
    LinkLabelRequest,
    MeasurementsModel,
    MoneyModel,
    OpenOrderRequest,
    OrderResponse,
    PickupReadinessResponse,
    ProhibitedNoticeResponse,
    PublicTrackingResponse,
    PublishRateRequest,
    QuoteResponse,
    ServiceabilityResponse,
    ShipmentDraftModel,
    ShipmentResponse,
    SupportCorrectionRequest,
    TariffRateResponse,
)
from ordering.application.amendment_service import (
    AmendmentOutcome,
    AmendmentService,
    ReceiverAmendment,
)
from ordering.application.catalogue_service import GoodsCatalogueService
from ordering.application.pricing_service import PricingService
from ordering.application.public_tracking_service import (
    PublicTrackingService,
    PublicTrackingView,
)
from ordering.application.send_service import (
    ReceiverDraft,
    SendService,
    ShipmentDraft,
)
from ordering.domain.entities import (
    GoodsCategory,
    Order,
    SenderRef,
    ShipmentRequest,
    TariffRate,
)
from ordering.domain.errors import OrderingError
from ordering.domain.money import Currency, Money
from ordering.domain.value_objects import (
    CancellationReason,
    DeliveryFeePayer,
    ParcelMeasurements,
    PaymentTerms,
    PriceQuote,
    RequestStatus,
    SenderKind,
    ShipmentAddOns,
)
from ordering.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    MerchantAccessPort,
    OrderingActor,
    OrderingAuthorizer,
    OrderingCommand,
)

router = APIRouter(prefix="/ordering", tags=["ordering"])
public_router = APIRouter(prefix="/track", tags=["public"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_send(request: Request) -> SendService:
    return _state(request, "send_service", "send")


def get_amendments(request: Request) -> AmendmentService:
    return _state(request, "amendment_service", "amendment")


def get_pricing(request: Request) -> PricingService:
    return _state(request, "pricing_service", "pricing")


def get_catalogue(request: Request) -> GoodsCatalogueService:
    return _state(request, "catalogue_service", "catalogue")


def get_tracking(request: Request) -> PublicTrackingService:
    return _state(request, "tracking_service", "tracking")


def get_authorizer(request: Request) -> OrderingAuthorizer:
    return _state(request, "authorizer", "authorizer")


def get_merchant_access(request: Request) -> MerchantAccessPort:
    return _state(request, "merchant_access", "merchant_access")


Send = Annotated[SendService, Depends(get_send)]
Amendments = Annotated[AmendmentService, Depends(get_amendments)]
Pricing = Annotated[PricingService, Depends(get_pricing)]
Catalogue = Annotated[GoodsCatalogueService, Depends(get_catalogue)]
Tracking = Annotated[PublicTrackingService, Depends(get_tracking)]
Authorizer = Annotated[OrderingAuthorizer, Depends(get_authorizer)]
MerchantAccess = Annotated[MerchantAccessPort, Depends(get_merchant_access)]


async def _authorize(
    *, authorizer: OrderingAuthorizer, header: str | None, command: OrderingCommand
) -> OrderingActor:
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


async def _assert_may_author_for_merchant(
    *, merchant_access: MerchantAccessPort, actor: OrderingActor, merchant_id: UUID
) -> None:
    """Only an owner may author shipments for a merchant.

    Operations is exempt. A team member is not: Customer App v3 states the keeper "cannot
    create, edit or cancel a shipment", and Ordering enforces that itself rather than
    assuming Merchant will never grant the permission.
    """
    if actor.is_operations:
        return
    try:
        access = await merchant_access.store_access(actor.principal_id)
    except AuthorizerUnavailableError:
        raise HTTPException(
            status_code=503, detail={"code": "merchant_access_unavailable"}
        ) from None

    membership = next(
        (entry for entry in access if entry.merchant_id == merchant_id), None
    )
    if membership is not None:
        if membership.may_author_shipments:
            return
        raise HTTPException(
            status_code=403,
            detail={
                "code": "team_member_may_not_author_shipments",
                "message": (
                    "a store team member prepares parcels and completes the courier "
                    "handover; they cannot create, edit or cancel a shipment"
                ),
            },
        )
    # Not a team member: the only remaining way in is owning the merchant, which the
    # token's merchant claim records because Identity issued it for this principal.
    if not actor.acts_for_merchant(merchant_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _require_own_shipment(
    send: SendService, request_id: UUID, actor: OrderingActor
) -> ShipmentRequest:
    try:
        shipment = send.get_request(request_id)
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    if actor.is_operations or actor.is_support:
        return shipment
    if shipment.sender.principal_id == actor.principal_id:
        return shipment
    if (
        shipment.sender.merchant_id is not None
        and actor.acts_for_merchant(shipment.sender.merchant_id)
    ):
        return shipment
    # 404 rather than 403 — confirming the id exists would leak someone else's parcel.
    raise HTTPException(status_code=404, detail={"code": "shipment_request_not_found"})


# ------------------------------------------------------------------ orders


@router.post("/orders", response_model=OrderResponse, status_code=201)
async def open_order(
    payload: OpenOrderRequest,
    send: Send,
    authorizer: Authorizer,
    merchant_access: MerchantAccess,
    authorization: BearerHeader = None,
) -> OrderResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.ORDER_CREATE,
    )
    if payload.merchant_id is not None:
        await _assert_may_author_for_merchant(
            merchant_access=merchant_access,
            actor=actor,
            merchant_id=payload.merchant_id,
        )
        sender = SenderRef(
            kind=SenderKind.MERCHANT,
            principal_id=actor.principal_id,
            merchant_id=payload.merchant_id,
            store_id=payload.store_id,
        )
    else:
        sender = SenderRef(
            kind=SenderKind.CUSTOMER, principal_id=actor.principal_id
        )
    try:
        order = send.open_order(sender=sender)
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _order_response(order)


@router.post("/orders/{order_id}/shipments", response_model=ShipmentResponse, status_code=201)
async def add_shipment(
    order_id: UUID,
    payload: ShipmentDraftModel,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_CREATE,
    )
    _assert_owns_order(send, order_id, actor)
    try:
        shipment = send.add_shipment(order_id=order_id, draft=_draft(payload))
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


@router.post(
    "/orders/{order_id}/shipments/bulk",
    response_model=list[ShipmentResponse],
    status_code=201,
)
async def add_shipments(
    order_id: UUID,
    payload: BulkShipmentRequest,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ShipmentResponse]:
    """Bulk creation (MER-18). All or nothing."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_CREATE,
    )
    _assert_owns_order(send, order_id, actor)
    try:
        created = send.add_shipments(
            order_id=order_id,
            drafts=tuple(_draft(item) for item in payload.shipments),
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return [_shipment_response(shipment) for shipment in created]


@router.get("/orders/{order_id}/shipments", response_model=list[ShipmentResponse])
async def list_shipments(
    order_id: UUID,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ShipmentResponse]:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_READ,
    )
    _assert_owns_order(send, order_id, actor)
    return [_shipment_response(item) for item in send.list_for_order(order_id)]


@router.get("/me/shipments", response_model=list[ShipmentResponse])
async def list_my_shipments(
    send: Send,
    authorizer: Authorizer,
    merchant_access: MerchantAccess,
    merchant_id: UUID | None = None,
    status: str | None = None,
    authorization: BearerHeader = None,
) -> list[ShipmentResponse]:
    """The signed-in sender's own parcels, across every order they created.

    Backs Customer App v3 `parcels`, the `home` active-parcel summary, and — with
    ``merchant_id`` — `storeShipments` and the workplace branch views.

    Identity is taken from the authenticated session and never from the request. A
    ``principal_id`` query parameter is deliberately not accepted: trusting a
    client-supplied identity here would let any signed-in caller read another person's
    parcels. ``merchant_id`` only *narrows* to a merchant the actor is already entitled
    to, and is refused otherwise.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_READ,
    )

    wanted = _parse_status_filter(status)

    if merchant_id is None:
        # Personal scope: what this principal authored. Operations and Support get their
        # own list too, not a cross-tenant dump — a broad read must name a merchant.
        found = send.list_for_principal(actor.principal_id)
    else:
        await _assert_may_read_merchant_parcels(
            merchant_access=merchant_access, actor=actor, merchant_id=merchant_id
        )
        found = send.list_for_merchant(merchant_id)

    return [
        _shipment_response(item)
        for item in found
        if wanted is None or item.status is wanted
    ]


def _parse_status_filter(status: str | None) -> RequestStatus | None:
    if status is None:
        return None
    try:
        return RequestStatus(status.strip().upper())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_status",
                "message": (
                    "unknown status filter; expected one of: "
                    + ", ".join(sorted(member.value for member in RequestStatus))
                ),
            },
        ) from None


async def _assert_may_read_merchant_parcels(
    *, merchant_access: MerchantAccessPort, actor: OrderingActor, merchant_id: UUID
) -> None:
    """Reading a merchant's parcels needs owner reach or an explicit read capability.

    A merchant the actor has no reach into answers 404 rather than 403, matching
    :func:`_assert_owns_order`, so the endpoint cannot be used to discover which merchant
    ids exist.
    """
    if actor.is_operations or actor.is_support:
        return
    if actor.acts_for_merchant(merchant_id):
        return
    try:
        access = await merchant_access.store_access(actor.principal_id)
    except AuthorizerUnavailableError:
        raise HTTPException(
            status_code=503, detail={"code": "merchant_access_unavailable"}
        ) from None

    membership = next(
        (entry for entry in access if entry.merchant_id == merchant_id), None
    )
    if membership is not None and membership.may_read_store_parcels:
        return
    raise HTTPException(status_code=404, detail={"code": "merchant_not_found"})


def _assert_owns_order(send: SendService, order_id: UUID, actor: OrderingActor) -> None:
    """Only the order's own sender may add to or read it.

    Operations and Support may look; nobody else may, and an order belonging to someone
    else answers 404 rather than 403 so its existence is not confirmed.
    """
    try:
        order = send.get_order(order_id)
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    if actor.is_operations or actor.is_support:
        return
    if order.sender.principal_id == actor.principal_id:
        return
    if order.sender.merchant_id is not None and actor.acts_for_merchant(
        order.sender.merchant_id
    ):
        return
    raise HTTPException(status_code=404, detail={"code": "order_not_found"})


# ------------------------------------------------------------------ labels


@router.post("/shipments/{request_id}/label", response_model=ShipmentResponse)
async def link_label(
    request_id: UUID,
    payload: LinkLabelRequest,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    """The merchant scans a pre-printed label onto the parcel (v6.3 p.10)."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.LABEL_LINK
    )
    _require_own_shipment(send, request_id, actor)
    try:
        shipment = send.link_label(request_id=request_id, label_code=payload.label_code)
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


# ------------------------------------------------------------------ pickup


@router.get("/orders/{order_id}/pickup-readiness", response_model=PickupReadinessResponse)
async def pickup_readiness(
    order_id: UUID,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PickupReadinessResponse:
    """MER-19 — "pickup time opens once every parcel carries a label"."""
    await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.SHIPMENT_READ
    )
    labelled, total = send.order_pickup_readiness(order_id=order_id)
    ready = total > 0 and labelled == total
    return PickupReadinessResponse(
        order_id=order_id,
        labelled=labelled,
        total=total,
        pickup_available=ready,
        blocked_reason=(
            None
            if ready
            else (
                f"{total - labelled} shipment(s) have no label yet — pickup time opens "
                "once every parcel carries a label"
            )
        ),
    )


@router.post("/orders/{order_id}/pickup", response_model=list[ShipmentResponse])
async def book_pickup(
    order_id: UUID,
    payload: BookPickupRequest,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ShipmentResponse]:
    await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.PICKUP_BOOK
    )
    try:
        booked = send.book_pickup(
            order_id=order_id,
            window_start=payload.window_start,
            window_end=payload.window_end,
            store_id=payload.store_id,
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return [_shipment_response(item) for item in booked]


# ------------------------------------------------------------------ registration


@router.post("/shipments/{request_id}/register", response_model=ShipmentResponse)
async def register_shipment(
    request_id: UUID,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    """Hand the parcel to Shipment — the last status Ordering owns."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_EDIT,
    )
    _require_own_shipment(send, request_id, actor)
    try:
        shipment, _ = send.register(request_id=request_id)
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


# ------------------------------------------------------------------ amendments


@router.get("/shipments/{request_id}", response_model=ShipmentResponse)
async def read_shipment(
    request_id: UUID,
    send: Send,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_READ,
    )
    return _shipment_response(_require_own_shipment(send, request_id, actor))


@router.patch("/shipments/{request_id}", response_model=AmendmentResponse)
async def amend_shipment(
    request_id: UUID,
    payload: AmendShipmentRequest,
    send: Send,
    amendments: Amendments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> AmendmentResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_EDIT,
    )
    _require_own_shipment(send, request_id, actor)
    try:
        outcome = amendments.amend(
            request_id=request_id,
            receiver=_receiver_amendment(payload.receiver),
            description=payload.description,
            goods_category_code=payload.goods_category_code,
            add_ons=_add_ons(payload.add_ons),
            pickup_store_id=payload.pickup_store_id,
            pickup_window_start=payload.pickup_window_start,
            pickup_window_end=payload.pickup_window_end,
            rejected_keys=payload.extra_keys(),
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _amendment_response(outcome)


@router.post("/shipments/{request_id}/cod-amount", response_model=ShipmentResponse)
async def change_cod_amount(
    request_id: UUID,
    payload: ChangeCodRequest,
    send: Send,
    amendments: Amendments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    """SHP-12 — the change must reach the courier before they reach the receiver."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_EDIT,
    )
    _require_own_shipment(send, request_id, actor)
    try:
        shipment = amendments.change_cod_amount(
            request_id=request_id, amount=_money(payload.amount)
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


@router.post("/shipments/{request_id}/support-correction", response_model=ShipmentResponse)
async def support_correction(
    request_id: UUID,
    payload: SupportCorrectionRequest,
    amendments: Amendments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    """"Support applies it before the delivery attempt." Support and Operations only."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SUPPORT_CORRECTION,
    )
    if not actor.can_correct_in_custody:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        shipment = amendments.apply_support_correction(
            request_id=request_id,
            receiver=_receiver_amendment(payload.receiver),
            cod_amount=_money(payload.cod_amount) if payload.cod_amount else None,
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


@router.post("/shipments/{request_id}/cancel", response_model=ShipmentResponse)
async def cancel_shipment(
    request_id: UUID,
    payload: CancelShipmentRequest,
    send: Send,
    amendments: Amendments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ShipmentResponse:
    """SHP-10 — cancellable only while nothing has entered custody."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.SHIPMENT_CANCEL,
    )
    _require_own_shipment(send, request_id, actor)
    try:
        shipment, _ = amendments.cancel(
            request_id=request_id, reason=CancellationReason(payload.reason)
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _shipment_response(shipment)


# ------------------------------------------------------------------ pricing


@router.get("/serviceability", response_model=ServiceabilityResponse)
async def read_serviceability(
    pricing: Pricing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ServiceabilityResponse:
    await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.QUOTE_READ
    )
    policy = pricing.serviceability
    return ServiceabilityResponse(
        configured=policy.is_configured,
        governorates=sorted(policy.serviceable_governorates),
    )


@router.get("/quote", response_model=QuoteResponse)
async def read_quote(
    origin_governorate: str,
    destination_governorate: str,
    pricing: Pricing,
    authorizer: Authorizer,
    hudhud_packaging: bool = False,
    authorization: BearerHeader = None,
) -> QuoteResponse:
    await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.QUOTE_READ
    )
    try:
        quote = pricing.quote(
            origin_governorate=origin_governorate,
            destination_governorate=destination_governorate,
            hudhud_packaging=hudhud_packaging,
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _quote_response(quote, origin_governorate, destination_governorate)


@router.post("/tariff-rates", response_model=TariffRateResponse, status_code=201)
async def publish_rate(
    payload: PublishRateRequest,
    pricing: Pricing,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> TariffRateResponse:
    """Load an approved rate. Operations only — a rate is a financial decision."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=OrderingCommand.TARIFF_WRITE
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        rate = pricing.publish_rate(
            reference=payload.reference,
            origin_governorate=payload.origin_governorate,
            destination_governorate=payload.destination_governorate,
            delivery_fee_minor_units=payload.delivery_fee.minor_units,
            packaging_fee_minor_units=payload.packaging_fee.minor_units,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except OrderingError as exc:
        raise_http_for_domain_error(exc)
    return _rate_response(rate)


# ------------------------------------------------------------------ catalogue


@router.get("/goods-categories", response_model=list[GoodsCategoryResponse])
async def list_goods_categories(
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[GoodsCategoryResponse]:
    await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.CATALOGUE_READ,
    )
    return [_category_response(item) for item in catalogue.list_categories()]


@router.get("/prohibited-goods", response_model=ProhibitedNoticeResponse)
async def read_prohibited_notice(
    catalogue: Catalogue,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ProhibitedNoticeResponse:
    """MER-22 — shown during send, before a sender may continue."""
    await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=OrderingCommand.CATALOGUE_READ,
    )
    notice = catalogue.prohibited_notice()
    return ProhibitedNoticeResponse(summary=notice.summary, detail=notice.detail)


# ------------------------------------------------------------------ public


@public_router.get("/{tracking_code}", response_model=PublicTrackingResponse)
async def public_track(tracking_code: str, tracking: Tracking) -> PublicTrackingResponse:
    """CUS-08 — track by code without signing in.

    Unknown, malformed and cancelled all answer 404 alike, so the endpoint cannot be used
    to confirm which codes exist.
    """
    view = tracking.lookup(tracking_code)
    if view is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return _tracking_response(view)


# ------------------------------------------------------------------ conversions


def _money(model: MoneyModel) -> Money:
    return Money(model.minor_units, Currency(model.currency))


def _money_model(amount: Money) -> MoneyModel:
    return MoneyModel(minor_units=amount.minor_units, currency=amount.currency.value)


def _measurements(model: MeasurementsModel | None) -> ParcelMeasurements:
    if model is None:
        return ParcelMeasurements()
    return ParcelMeasurements(
        weight_grams=model.weight_grams,
        length_cm=model.length_cm,
        width_cm=model.width_cm,
        height_cm=model.height_cm,
    )


def _add_ons(model: AddOnsModel | None) -> ShipmentAddOns | None:
    if model is None:
        return None
    return ShipmentAddOns(
        open_box_allowed=model.open_box_allowed,
        photo_documentation=model.photo_documentation,
        hudhud_packaging=model.hudhud_packaging,
        delivery_fee_payer=DeliveryFeePayer(model.delivery_fee_payer),
    )


def _receiver_amendment(model) -> ReceiverAmendment | None:
    if model is None:
        return None
    return ReceiverAmendment(
        phone=model.phone,
        governorate=model.governorate,
        name=model.name,
        address_line=model.address_line,
        landmark=model.landmark,
    )


def _draft(model: ShipmentDraftModel) -> ShipmentDraft:
    return ShipmentDraft(
        receiver=ReceiverDraft(
            phone=model.receiver.phone,
            governorate=model.receiver.governorate,
            name=model.receiver.name,
            address_line=model.receiver.address_line,
            landmark=model.receiver.landmark,
        ),
        description=model.description,
        goods_category_code=model.goods_category_code,
        measurements=_measurements(model.measurements),
        payment_terms=PaymentTerms(model.payment_terms),
        cod_amount=_money(model.cod_amount) if model.cod_amount else None,
        add_ons=_add_ons(model.add_ons),
        pickup_store_id=model.pickup_store_id,
        prohibited_goods_acknowledged=model.prohibited_goods_acknowledged,
    )


def _order_response(order: Order) -> OrderResponse:
    return OrderResponse(
        order_id=order.order_id,
        reference=order.reference,
        sender_kind=order.sender.kind.value,
        status=order.status.value,
        version=order.version,
    )


def _shipment_response(shipment: ShipmentRequest) -> ShipmentResponse:
    return ShipmentResponse(
        request_id=shipment.request_id,
        order_id=shipment.order_id,
        tracking_code=shipment.tracking_code,
        status=shipment.status.value,
        sender_kind=shipment.sender.kind.value,
        description=shipment.description,
        goods_category_code=shipment.goods_category_code,
        receiver_governorate=shipment.receiver.governorate,
        receiver_phone_last4=shipment.receiver.phone_last4,
        receiver_name=shipment.receiver.name,
        payment_terms=shipment.payment_terms.value,
        cod_amount=(
            _money_model(shipment.cod_amount) if shipment.cod_amount else None
        ),
        open_box_allowed=shipment.add_ons.open_box_allowed,
        photo_documentation=shipment.add_ons.photo_documentation,
        hudhud_packaging=shipment.add_ons.hudhud_packaging,
        delivery_fee_payer=shipment.add_ons.delivery_fee_payer.value,
        label_code=shipment.label_code,
        requires_label=shipment.requires_label,
        pickup_window_start=shipment.pickup_window_start,
        pickup_window_end=shipment.pickup_window_end,
        edit_stage=shipment.edit_stage.value,
        editable_fields=sorted(shipment.self_service_editable_fields()),
        version=shipment.version,
    )


def _amendment_response(outcome: AmendmentOutcome) -> AmendmentResponse:
    return AmendmentResponse(
        shipment=_shipment_response(outcome.request),
        courier_released=outcome.courier_released,
        changed_fields=list(outcome.changed_fields),
    )


def _quote_response(quote: PriceQuote, origin: str, destination: str) -> QuoteResponse:
    return QuoteResponse(
        origin_governorate=origin.strip().upper(),
        destination_governorate=destination.strip().upper(),
        delivery_fee=_money_model(quote.delivery_fee),
        packaging_fee=_money_model(quote.packaging_fee),
        total=_money_model(quote.total),
        tariff_reference=quote.tariff_reference,
    )


def _rate_response(rate: TariffRate) -> TariffRateResponse:
    return TariffRateResponse(
        tariff_id=rate.tariff_id,
        reference=rate.reference,
        origin_governorate=rate.origin_governorate,
        destination_governorate=rate.destination_governorate,
        delivery_fee=_money_model(rate.delivery_fee),
        packaging_fee=_money_model(rate.packaging_fee),
        effective_from=rate.effective_from,
        effective_to=rate.effective_to,
    )


def _category_response(category: GoodsCategory) -> GoodsCategoryResponse:
    return GoodsCategoryResponse(
        code=category.code,
        display_name=category.display_name,
        hint=category.hint,
        restriction_note=category.restriction_note,
        prohibited=category.prohibited,
    )


def _tracking_response(view: PublicTrackingView) -> PublicTrackingResponse:
    return PublicTrackingResponse(
        tracking_code=view.tracking_code,
        status=view.status,
        destination_governorate=view.destination_governorate,
        created_at=view.created_at,
        registered_at=view.registered_at,
    )
