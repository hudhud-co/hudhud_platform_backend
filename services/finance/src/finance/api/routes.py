"""Finance HTTP adapter.

The authorization rule running through every route is ADR-0012's separation of duties: a
driver records their own deposit, a **cashier or accountant** confirms it, and only
**Operations** approves a payout. Each is a different person because the one who hands
money over is never the one who says it arrived.

Merchant-scoped reads are scoped by the *authenticated* merchant, never by a merchant id
in the path alone — otherwise any merchant owner could read any other's balance.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from finance.api.errors import raise_http_for_domain_error
from finance.api.schemas import (
    BalanceResponse,
    BlockCodRequest,
    CashExposureResponse,
    CashPositionResponse,
    ChargeRefusalRequest,
    CollectionResponse,
    ConfirmHubReturnRequest,
    DepositResponse,
    JournalEntryResponse,
    MediaRefModel,
    MerchantBalanceResponse,
    MoneyModel,
    OpenCashAccountRequest,
    OpenItemsResponse,
    OpenReconciliationRequest,
    PayoutResponse,
    PostingResponse,
    RecogniseRefundRequest,
    ReconciliationResponse,
    RecordCollectionRequest,
    RecordPickupFeeRequest,
    RefusalChargeResponse,
    RejectDepositRequest,
    RejectPayoutRequest,
    RequestPayoutRequest,
    ResolveReconciliationRequest,
    SetCashLimitRequest,
    SettlementHistoryItem,
    SubmitDepositRequest,
    SubmitDepositResponse,
    WaiveReturnFeeRequest,
)
from finance.application.cash_service import CashPosition, CashService
from finance.application.cod_service import CodService
from finance.application.settlement_service import MerchantBalance, SettlementService
from finance.domain.entities import (
    CashExposure,
    CodCollection,
    Deposit,
    PayoutRequest,
    RouteReconciliation,
    SettlementHistoryEntry,
)
from finance.domain.errors import FinanceError
from finance.domain.ledger import Balance, JournalEntry, balance_of
from finance.domain.money import Currency, Money
from finance.domain.value_objects import AccountKind, AccountRef, EvidenceMediaRef
from finance.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    FinanceActor,
    FinanceAuthorizer,
    FinanceCommand,
)
from finance.ports.repository import FinanceUnitOfWork

router = APIRouter(prefix="/finance", tags=["finance"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_unit_of_work(request: Request) -> FinanceUnitOfWork:
    """One unit of work per request, built here and used by nothing else.

    FastAPI caches a dependency's result for the lifetime of a single request, so every
    service below shares *this* request's transaction and no other request's. Returning
    a long-lived instance instead is the defect this replaced: one session served every
    caller at once.
    """
    factory = _state(request, "unit_of_work_factory", "persistence")
    return factory()


UnitOfWork = Annotated[FinanceUnitOfWork, Depends(get_unit_of_work)]


def get_cash(request: Request, unit_of_work: UnitOfWork) -> CashService:
    return CashService(
        unit_of_work,
        default_cash_limit=request.app.state.cash_limit,
        outbox_max_attempts=request.app.state.settings.outbox_max_attempts,
    )


def get_cod(request: Request, unit_of_work: UnitOfWork) -> CodService:
    return CodService(
        unit_of_work,
        outbox_max_attempts=request.app.state.settings.outbox_max_attempts,
    )


def get_settlement(request: Request, unit_of_work: UnitOfWork) -> SettlementService:
    settings = request.app.state.settings
    return SettlementService(
        unit_of_work,
        return_trip_fee=request.app.state.return_trip_fee,
        return_fee_waiver_permitted=settings.return_fee_waiver_permitted,
        outbox_max_attempts=settings.outbox_max_attempts,
    )


def get_authorizer(request: Request) -> FinanceAuthorizer:
    return _state(request, "authorizer", "authorizer")


Cash = Annotated[CashService, Depends(get_cash)]
Cod = Annotated[CodService, Depends(get_cod)]
Settlement = Annotated[SettlementService, Depends(get_settlement)]
Authorizer = Annotated[FinanceAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: FinanceAuthorizer, header: str | None, command: FinanceCommand
) -> FinanceActor:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_bearer_token"})
    token = header.split(" ", 1)[1].strip()
    try:
        decision = await authorizer.authorize(bearer_token=token, command=command)
    except AuthorizerUnavailableError:
        # Identity being unreachable says nothing about this caller.
        raise HTTPException(
            status_code=503, detail={"code": "authorization_unavailable"}
        ) from None
    if decision.outcome is AuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated"})
    if decision.outcome is not AuthorizationOutcome.ALLOWED or decision.actor is None:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return decision.actor


class _domain_errors:  # noqa: N801 - a context manager used as a statement
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None or isinstance(exc, HTTPException):
            return False
        if isinstance(exc, FinanceError):
            raise_http_for_domain_error(exc)
        return False


def _assert_own_driver_record(actor: FinanceActor, driver_principal_id: UUID) -> None:
    """A driver sees their own cash. Operations and accountants see anyone's."""
    if actor.principal_id == driver_principal_id:
        return
    if actor.may_see_platform_cash_exposure or actor.is_cashier:
        return
    raise HTTPException(status_code=403, detail={"code": "forbidden"})


def _assert_own_merchant(actor: FinanceActor, merchant_id: UUID) -> None:
    if actor.merchant_id == merchant_id:
        return
    if actor.is_operations or actor.is_accountant:
        return
    raise HTTPException(status_code=403, detail={"code": "forbidden"})


# ------------------------------------------------------------------ COD


@router.post("/collections", response_model=CollectionResponse, status_code=201)
async def record_collection(
    payload: RecordCollectionRequest,
    cod: Cod,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CollectionResponse:
    """PAY-01 — record what was taken for one parcel and post it.

    ``idempotency_key`` makes a retry safe: the same key finds the entry it already made
    instead of posting the money a second time.
    """
    await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.COD_RECORD
    )
    with _domain_errors():
        result = cod.record_collection(
            tracking_code=payload.tracking_code,
            merchant_id=payload.merchant_id,
            channel=payload.channel,
            goods_amount=_money(payload.goods_amount),
            delivery_fee=_money(payload.delivery_fee),
            driver_principal_id=payload.driver_principal_id,
            idempotency_key=payload.idempotency_key,
        )
    return _collection_response(result.collection)


@router.get("/collections/{tracking_code}", response_model=CollectionResponse)
async def read_collection(
    tracking_code: str,
    cod: Cod,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CollectionResponse:
    await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.LEDGER_READ
    )
    with _domain_errors():
        collection = cod.collection_for(tracking_code=tracking_code)
    return _collection_response(collection)


@router.post("/pickup-fees", response_model=JournalEntryResponse, status_code=201)
async def record_pickup_fee(
    payload: RecordPickupFeeRequest,
    cod: Cod,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> JournalEntryResponse:
    """DRV-A11, PAY-10 — entirely HUDHUD's revenue; nothing is owed to the merchant."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.COD_RECORD
    )
    with _domain_errors():
        entry = cod.record_pickup_fee(
            tracking_code=payload.tracking_code,
            merchant_id=payload.merchant_id,
            amount=_money(payload.amount),
            driver_principal_id=actor.principal_id,
            by_card=payload.by_card,
        )
    return _entry_response(entry)


@router.post(
    "/collections/{tracking_code}/refund",
    response_model=JournalEntryResponse,
    status_code=201,
)
async def recognise_refund(
    tracking_code: str,
    payload: RecogniseRefundRequest,
    cod: Cod,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> JournalEntryResponse:
    """PAY-09 — v6.3 p.39: HUDHUD refunds only a receiver who paid HUDHUD directly."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.REFUND_RECOGNISE,
    )
    if not (actor.is_operations or actor.is_accountant or actor.is_support):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        entry = cod.recognise_refund(
            tracking_code=tracking_code,
            receiver_principal_id=payload.receiver_principal_id,
            amount=_money(payload.amount),
            actor_id=actor.principal_id,
        )
    return _entry_response(entry)


# ------------------------------------------------------------------ cash custody


@router.post("/cash-accounts", response_model=CashPositionResponse, status_code=201)
async def open_cash_account(
    payload: OpenCashAccountRequest,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CashPositionResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.CASH_LIMIT_SET,
    )
    if not actor.may_see_platform_cash_exposure:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        cash.open_account(
            driver_principal_id=payload.driver_principal_id,
            limit=_money(payload.limit) if payload.limit else None,
        )
        position = cash.position_for(driver_principal_id=payload.driver_principal_id)
    return _position_response(position)


@router.get(
    "/cash-accounts/{driver_principal_id}", response_model=CashPositionResponse
)
async def read_cash_position(
    driver_principal_id: UUID,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CashPositionResponse:
    """DRV-A05, DRV-A06 — a driver's own cash and how much of their limit is used."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.CASH_ACCOUNT_READ,
    )
    _assert_own_driver_record(actor, driver_principal_id)
    with _domain_errors():
        position = cash.position_for(driver_principal_id=driver_principal_id)
    return _position_response(position)


@router.put(
    "/cash-accounts/{driver_principal_id}/limit", response_model=CashPositionResponse
)
async def set_cash_limit(
    driver_principal_id: UUID,
    payload: SetCashLimitRequest,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CashPositionResponse:
    """A driver does not set their own limit."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.CASH_LIMIT_SET,
    )
    if not actor.may_see_platform_cash_exposure:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        cash.set_limit(
            driver_principal_id=driver_principal_id, limit=_money(payload.limit)
        )
        position = cash.position_for(driver_principal_id=driver_principal_id)
    return _position_response(position)


@router.put(
    "/cash-accounts/{driver_principal_id}/cod-block",
    response_model=CashPositionResponse,
)
async def block_cod(
    driver_principal_id: UUID,
    payload: BlockCodRequest,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CashPositionResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.CASH_LIMIT_SET,
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        cash.block_cod(
            driver_principal_id=driver_principal_id,
            reason=payload.reason,
            blocked=payload.blocked,
        )
        position = cash.position_for(driver_principal_id=driver_principal_id)
    return _position_response(position)


@router.get("/cash-exposure", response_model=list[CashExposureResponse])
async def read_cash_exposure(
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[CashExposureResponse]:
    """OPS-04 — which drivers hold how much, worst first."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.CASH_EXPOSURE_READ,
    )
    if not actor.may_see_platform_cash_exposure:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        rows = cash.cash_exposure()
    return [_exposure_response(row) for row in rows]


# ------------------------------------------------------------------ deposits


@router.post("/deposits", response_model=SubmitDepositResponse, status_code=201)
async def submit_deposit(
    payload: SubmitDepositRequest,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> SubmitDepositResponse:
    """DRV-A07 — the driver records their own deposit, and only their own.

    The depositing driver is the authenticated one: a body-supplied id would let one
    driver clear another's cash off their record.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.DEPOSIT_SUBMIT,
    )
    with _domain_errors():
        result = cash.submit_deposit(
            driver_principal_id=actor.principal_id,
            method=payload.method,
            amount=_money(payload.amount),
            reference=payload.reference,
            receipt=_media(payload.receipt),
            hub_id=payload.hub_id,
        )
    return SubmitDepositResponse(
        deposit=_deposit_response(result.deposit),
        held_after=_money_model(result.held_after),
    )


@router.post("/deposits/{deposit_id}/confirm", response_model=DepositResponse)
async def confirm_deposit(
    deposit_id: UUID,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DepositResponse:
    """The second pair of eyes. Which pair depends on the method (ADR-0012)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.DEPOSIT_DECIDE,
    )
    with _domain_errors():
        deposit = cash.confirm_deposit(deposit_id=deposit_id, actor=actor)
    return _deposit_response(deposit)


@router.post("/deposits/{deposit_id}/reject", response_model=DepositResponse)
async def reject_deposit(
    deposit_id: UUID,
    payload: RejectDepositRequest,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DepositResponse:
    """The cash goes back into the driver's custody until somebody finds it."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.DEPOSIT_DECIDE,
    )
    with _domain_errors():
        deposit = cash.reject_deposit(
            deposit_id=deposit_id, actor=actor, reason=payload.reason
        )
    return _deposit_response(deposit)


@router.get("/deposits/pending", response_model=list[DepositResponse])
async def list_pending_deposits(
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[DepositResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.DEPOSIT_READ
    )
    if not (actor.may_confirm_a_hub_deposit or actor.may_see_platform_cash_exposure):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        deposits = cash.pending_verification()
    return [_deposit_response(deposit) for deposit in deposits]


@router.get(
    "/cash-accounts/{driver_principal_id}/settlements",
    response_model=list[SettlementHistoryItem],
)
async def read_settlement_history(
    driver_principal_id: UUID,
    cash: Cash,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[SettlementHistoryItem]:
    """DRV-A10 — every settlement with the receipt it was made against."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.DEPOSIT_READ
    )
    _assert_own_driver_record(actor, driver_principal_id)
    with _domain_errors():
        history = cash.settlement_history(driver_principal_id=driver_principal_id)
    return [_history_response(item) for item in history]


# ------------------------------------------------------------------ merchant


@router.get("/merchants/{merchant_id}/balance", response_model=MerchantBalanceResponse)
async def read_merchant_balance(
    merchant_id: UUID,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MerchantBalanceResponse:
    """PAY-05 — derived from the ledger every time it is asked for."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.MERCHANT_BALANCE_READ,
    )
    _assert_own_merchant(actor, merchant_id)
    with _domain_errors():
        balance = settlement.balance_for(merchant_id=merchant_id)
    return _balance_response(balance)


@router.post(
    "/merchants/{merchant_id}/payouts", response_model=PayoutResponse, status_code=201
)
async def request_payout(
    merchant_id: UUID,
    payload: RequestPayoutRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PayoutResponse:
    """PAY-06 — one of the four methods v6.3 names.

    Paying it out is a separate step and is blocked by PAY-07; everything up to that
    point is implemented.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.PAYOUT_REQUEST,
    )
    _assert_own_merchant(actor, merchant_id)
    with _domain_errors():
        payout = settlement.request_payout(
            merchant_id=merchant_id,
            method=payload.method,
            amount=_money(payload.amount),
            destination_reference=payload.destination_reference,
            requested_by_principal_id=actor.principal_id,
        )
    return _payout_response(payout)


@router.get("/merchants/{merchant_id}/payouts", response_model=list[PayoutResponse])
async def list_merchant_payouts(
    merchant_id: UUID,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[PayoutResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_READ
    )
    _assert_own_merchant(actor, merchant_id)
    with _domain_errors():
        payouts = settlement.payouts_for(merchant_id=merchant_id)
    return [_payout_response(payout) for payout in payouts]


@router.get("/payouts/open", response_model=list[PayoutResponse])
async def list_open_payouts(
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[PayoutResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_READ
    )
    if not (actor.is_operations or actor.is_accountant):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        payouts = settlement.open_payouts()
    return [_payout_response(payout) for payout in payouts]


@router.post("/payouts/{payout_id}/approve", response_model=PayoutResponse)
async def approve_payout(
    payout_id: UUID,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PayoutResponse:
    """ADR-0012 — only Operations."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_DECIDE
    )
    with _domain_errors():
        payout = settlement.approve_payout(payout_id=payout_id, actor=actor)
    return _payout_response(payout)


@router.post("/payouts/{payout_id}/reject", response_model=PayoutResponse)
async def reject_payout(
    payout_id: UUID,
    payload: RejectPayoutRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PayoutResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_DECIDE
    )
    with _domain_errors():
        payout = settlement.reject_payout(
            payout_id=payout_id, actor=actor, reason=payload.reason
        )
    return _payout_response(payout)


@router.post("/payouts/{payout_id}/pay", response_model=PayoutResponse)
async def pay_payout(
    payout_id: UUID,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PayoutResponse:
    """PAY-07 — answers 501, and is the only route that does so unconditionally.

    v6.3 Appendix A p.44 leaves the operating procedure per payout method to an
    accountant: what fee applies, when the money lands, and how the transfer is
    reconciled. Everything checkable is checked first, so the 501 means "nobody has
    decided", not "this request was wrong".
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_PAY
    )
    with _domain_errors():
        payout = settlement.pay_payout(payout_id=payout_id, actor=actor)
    return _payout_response(payout)


# ------------------------------------------------------------------ PAY-08


@router.post("/refusal-charges", response_model=RefusalChargeResponse, status_code=201)
async def charge_for_refusal(
    payload: ChargeRefusalRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> RefusalChargeResponse:
    """v6.3 p.38, p.39 — both fees, regardless of reason. Implemented, not blocked."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.COD_RECORD
    )
    if not (actor.is_operations or actor.is_accountant):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        charge = settlement.charge_for_refusal(
            tracking_code=payload.tracking_code,
            merchant_id=payload.merchant_id,
            delivery_fee=_money(payload.delivery_fee),
            actor_id=actor.principal_id,
        )
    return RefusalChargeResponse(
        tracking_code=charge.tracking_code,
        merchant_id=charge.merchant_id,
        delivery_fee=_money_model(charge.delivery_fee),
        return_trip_fee=_money_model(charge.return_trip_fee),
        total=_money_model(charge.total),
    )


@router.post("/refusal-charges/waive", response_model=JournalEntryResponse)
async def waive_return_trip_fee(
    payload: WaiveReturnFeeRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> JournalEntryResponse:
    """PAY-08 — answers 501 while the Open Item stands.

    The charge above is not blocked, because v6.3 settles it. Only the waiver is, and it
    is written in full behind a flag that is off — the day an accountant decides HUDHUD
    may waive, setting ``FINANCE_RETURN_FEE_WAIVER_PERMITTED`` opens this route with no
    code change.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.PAYOUT_DECIDE
    )
    with _domain_errors():
        entry = settlement.waive_return_trip_fee(
            tracking_code=payload.tracking_code,
            merchant_id=payload.merchant_id,
            amount=_money(payload.amount),
            actor=actor,
            note=payload.note,
        )
    return _entry_response(entry)


# ------------------------------------------------------------------ reconciliation


@router.post(
    "/reconciliations", response_model=ReconciliationResponse, status_code=201
)
async def open_reconciliation(
    payload: OpenReconciliationRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ReconciliationResponse:
    """DRV-A13 — the count, against what the ledger says the driver is holding."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.RECONCILIATION_OPEN,
    )
    if not (actor.may_confirm_a_hub_deposit or actor.may_see_platform_cash_exposure):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        reconciliation = settlement.open_reconciliation(
            driver_principal_id=payload.driver_principal_id,
            route_day=payload.route_day,
            counted=_money(payload.counted),
            parcels_returned_confirmed=payload.parcels_returned_confirmed,
        )
    return _reconciliation_response(reconciliation)


@router.post(
    "/reconciliations/{reconciliation_id}/hub-return",
    response_model=ReconciliationResponse,
)
async def confirm_hub_return(
    reconciliation_id: UUID,
    payload: ConfirmHubReturnRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ReconciliationResponse:
    """DRV-A12 — parcels handed over and cash settled, each confirmed."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.RECONCILIATION_OPEN,
    )
    if not (actor.may_confirm_a_hub_deposit or actor.may_see_platform_cash_exposure):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        reconciliation = settlement.confirm_hub_return(
            reconciliation_id=reconciliation_id,
            parcels=payload.parcels,
            cash=payload.cash,
        )
    return _reconciliation_response(reconciliation)


@router.post(
    "/reconciliations/{reconciliation_id}/resolve",
    response_model=ReconciliationResponse,
)
async def resolve_reconciliation(
    reconciliation_id: UUID,
    payload: ResolveReconciliationRequest,
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ReconciliationResponse:
    """A mismatch is investigated before payout (v6.3 p.31, p.33)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.RECONCILIATION_RESOLVE,
    )
    with _domain_errors():
        result = settlement.resolve_reconciliation(
            reconciliation_id=reconciliation_id,
            actor=actor,
            note=payload.note,
            post_adjustment=payload.post_adjustment,
        )
    return _reconciliation_response(result.reconciliation)


@router.get("/reconciliations/unresolved", response_model=list[ReconciliationResponse])
async def list_unresolved_reconciliations(
    settlement: Settlement,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ReconciliationResponse]:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=FinanceCommand.RECONCILIATION_READ,
    )
    if not actor.may_see_platform_cash_exposure:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        rows = settlement.unresolved_reconciliations()
    return [_reconciliation_response(row) for row in rows]


# ------------------------------------------------------------------ the ledger


@router.get("/balances/{account_kind}", response_model=BalanceResponse)
async def read_balance(
    account_kind: AccountKind,
    unit_of_work: UnitOfWork,
    authorizer: Authorizer,
    party_id: UUID | None = None,
    authorization: BearerHeader = None,
) -> BalanceResponse:
    """Any account's balance, summed from its postings.

    Only an accountant or Operations may read a platform account: a merchant payable
    read by the wrong party is another merchant's business.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.LEDGER_READ
    )
    if not (actor.is_operations or actor.is_accountant):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        try:
            account = AccountRef(kind=account_kind, party_id=party_id)
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "account_party_mismatch"}
            ) from None
        unit_of_work.begin()
        try:
            balance = balance_of(
                account, unit_of_work.ledger.entries_for_account(account)
            )
            unit_of_work.commit()
        except Exception:
            unit_of_work.rollback()
            raise
    return _balance_of_response(balance)


@router.get("/open-items", response_model=OpenItemsResponse)
async def read_open_items(
    settlement: Settlement,
    request: Request,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> OpenItemsResponse:
    """What this service will not do, and why (v6.3 Appendix A p.44).

    Reporting it beats letting an operator meet a 501 and guess whether it is a bug.
    """
    await _authorize(
        authorizer=authorizer, header=authorization, command=FinanceCommand.LEDGER_READ
    )
    settings = request.app.state.settings
    return OpenItemsResponse(
        payout_payment_available=False,
        payout_payment_blocked_by=(
            "PAY-07 — the operating procedure per payout method is a v6.3 Appendix A "
            "Open Item and must be defined by an accountant"
        ),
        return_trip_fee_configured=settings.return_trip_fee_configured,
        return_fee_waiver_permitted=settlement.return_fee_waiver_permitted,
        return_fee_waiver_blocked_by=(
            "PAY-08 — whether HUDHUD may waive the return-trip fee is a v6.3 Appendix A "
            "Open Item; the charge itself is implemented and not blocked"
        ),
    )


# ------------------------------------------------------------------ conversions


def _money(model: MoneyModel) -> Money:
    return Money(minor_units=model.minor_units, currency=Currency(model.currency))


def _money_model(amount: Money) -> MoneyModel:
    return MoneyModel(
        minor_units=amount.minor_units, currency=amount.currency.value
    )


def _media(model: MediaRefModel) -> EvidenceMediaRef:
    return EvidenceMediaRef(
        bucket=model.bucket, key=model.key, content_type=model.content_type
    )


def _media_model(media: EvidenceMediaRef) -> MediaRefModel:
    return MediaRefModel(
        bucket=media.bucket, key=media.key, content_type=media.content_type
    )


def _collection_response(collection: CodCollection) -> CollectionResponse:
    return CollectionResponse(
        collection_id=collection.collection_id,
        tracking_code=collection.tracking_code,
        merchant_id=collection.merchant_id,
        channel=collection.channel,
        goods_amount=_money_model(collection.goods_amount),
        delivery_fee=_money_model(collection.delivery_fee),
        total=_money_model(collection.total),
        collected_at=collection.collected_at,
        driver_principal_id=collection.driver_principal_id,
        is_paid=collection.is_paid,
        settled_at=collection.settled_at,
        settling_deposit_id=collection.settling_deposit_id,
        journal_entry_id=collection.journal_entry_id,
    )


def _entry_response(entry: JournalEntry) -> JournalEntryResponse:
    return JournalEntryResponse(
        entry_id=entry.entry_id,
        reason=entry.reason,
        occurred_at=entry.occurred_at,
        total=_money_model(entry.total),
        postings=[
            PostingResponse(
                account_kind=posting.account.kind,
                party_id=posting.account.party_id,
                side=posting.side,
                amount=_money_model(posting.amount),
            )
            for posting in entry.postings
        ],
        subject_kind=entry.subject_kind,
        subject_id=entry.subject_id,
        corrects_entry_id=entry.corrects_entry_id,
    )


def _balance_of_response(balance: Balance) -> BalanceResponse:
    return BalanceResponse(
        account_kind=balance.account.kind,
        party_id=balance.account.party_id,
        balance=_money_model(balance.amount),
        posting_count=balance.posting_count,
    )


def _position_response(position: CashPosition) -> CashPositionResponse:
    return CashPositionResponse(
        driver_principal_id=position.driver_principal_id,
        held=_money_model(position.held),
        limit=_money_model(position.limit),
        headroom=_money_model(position.headroom),
        utilisation_percent=position.utilisation_percent,
        over_limit=position.over_limit,
        may_take_more_cod=position.may_take_more_cod,
        open_deposits=position.open_deposits,
    )


def _exposure_response(row: CashExposure) -> CashExposureResponse:
    return CashExposureResponse(
        driver_principal_id=row.driver_principal_id,
        held=_money_model(row.held),
        limit=_money_model(row.limit),
        utilisation_percent=row.utilisation_percent,
        over_limit=row.over_limit,
        open_deposits=row.open_deposits,
    )


def _deposit_response(deposit: Deposit) -> DepositResponse:
    return DepositResponse(
        deposit_id=deposit.deposit_id,
        driver_principal_id=deposit.driver_principal_id,
        method=deposit.method,
        amount=_money_model(deposit.amount),
        reference=deposit.reference,
        status=deposit.status,
        submitted_at=deposit.submitted_at,
        hub_id=deposit.hub_id,
        decided_at=deposit.decided_at,
        rejection_reason=deposit.rejection_reason,
        settles_the_parcels_it_covers=deposit.settles_the_parcels_it_covers,
    )


def _history_response(item: SettlementHistoryEntry) -> SettlementHistoryItem:
    return SettlementHistoryItem(
        deposit_id=item.deposit_id,
        method=item.method,
        amount=_money_model(item.amount),
        status=item.status,
        submitted_at=item.submitted_at,
        decided_at=item.decided_at,
        reference=item.reference,
        receipt=_media_model(item.receipt),
    )


def _balance_response(balance: MerchantBalance) -> MerchantBalanceResponse:
    return MerchantBalanceResponse(
        merchant_id=balance.merchant_id,
        balance=_money_model(balance.balance),
        available=_money_model(balance.available),
        committed=_money_model(balance.committed),
        payouts_blocked=balance.payouts_blocked,
        payouts_blocked_reason=balance.payouts_blocked_reason,
        open_payout_count=balance.open_payout_count,
    )


def _payout_response(payout: PayoutRequest) -> PayoutResponse:
    return PayoutResponse(
        payout_id=payout.payout_id,
        merchant_id=payout.merchant_id,
        method=payout.method,
        amount=_money_model(payout.amount),
        status=payout.status,
        # Whether one was given, never what it is.
        has_destination=payout.destination_reference is not None,
        requested_at=payout.requested_at,
        decided_at=payout.decided_at,
        rejection_reason=payout.rejection_reason,
        paid_at=payout.paid_at,
    )


def _reconciliation_response(
    reconciliation: RouteReconciliation,
) -> ReconciliationResponse:
    return ReconciliationResponse(
        reconciliation_id=reconciliation.reconciliation_id,
        driver_principal_id=reconciliation.driver_principal_id,
        route_day=reconciliation.route_day,
        expected=_money_model(reconciliation.expected),
        counted=_money_model(reconciliation.counted),
        difference=_money_model(reconciliation.difference),
        outcome=reconciliation.outcome,
        status=reconciliation.status,
        parcels_returned_confirmed=reconciliation.parcels_returned_confirmed,
        cash_settled_confirmed=reconciliation.cash_settled_confirmed,
        day_is_complete=reconciliation.day_is_complete,
        blocks_payout=reconciliation.blocks_payout,
        resolution_note=reconciliation.resolution_note,
        adjustment_entry_id=reconciliation.adjustment_entry_id,
    )
