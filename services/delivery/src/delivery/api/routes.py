"""Delivery HTTP adapter.

The recurring authorization rule is not a role but an identity: a driver acts on the
parcels in their own custody, and the driver principal used by every command is taken
from the authenticated actor, never from the request body. A body-supplied driver id
would let one driver close another's stop.

Two routes answer 501 by design. Verifying a delivery code needs a decided code length
(DRV-L05) and retaining an ID photograph needs a decided retention period (DRV-L07);
both are business decisions the sources contradict each other on, and both fail closed.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from delivery.api.errors import raise_http_for_domain_error
from delivery.api.schemas import (
    ArrivalViewResponse,
    AttachPhotoRequest,
    CardApprovalRequest,
    CardDeclineRequest,
    CollectCashRequest,
    ConfirmPrepaidRequest,
    CourierRatingSummaryResponse,
    DecideNextAttemptRequest,
    DeliveryCodePolicyResponse,
    DeliveryResponse,
    DepartRequest,
    FailedAttemptResponse,
    FailureResponse,
    GeoPointModel,
    HandoverPreferenceRequest,
    HandoverPreferenceResponse,
    InspectionRequest,
    IssueReportResponse,
    ManifestResponse,
    MediaRefModel,
    MoneyModel,
    OpenManifestRequest,
    ParcelSettingsModel,
    PaymentResponse,
    PhotoResponse,
    RaiseIncidentRequest,
    RateCourierRequest,
    RatingAcknowledgementResponse,
    RefusalRequest,
    ReportIssueRequest,
    ReturnedAfterHoldResponse,
    ScanParcelRequest,
    SealCheckRequest,
    StopResponse,
    TimeWindowModel,
    VerificationResponse,
    VerifyWithCodeRequest,
    VerifyWithIdRequest,
)
from delivery.application.doorstep_service import DoorstepService, ParcelForManifest
from delivery.application.outcome_service import OutcomeService
from delivery.application.payment_service import PaymentService
from delivery.application.receiver_service import ArrivalView, ReceiverService
from delivery.domain.delivery_code import CONFLICT_SOURCES, CONFLICTING_LENGTHS
from delivery.domain.entities import (
    CourierRating,
    CourierRatingSummary,
    DeliveryManifest,
    DeliveryStop,
    FailedAttempt,
    ParcelIssueReport,
    ParcelSettings,
    PaymentRecord,
    PhotoEvidence,
    ReceiverPreference,
)
from delivery.domain.errors import DeliveryError, NotThisDriversStop
from delivery.domain.money import Currency, Money
from delivery.domain.value_objects import (
    HOLD_DAYS,
    EvidenceMediaRef,
    GeoPoint,
    SealCheckOutcome,
    TimeWindow,
)
from delivery.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    DeliveryActor,
    DeliveryAuthorizer,
    DeliveryCommand,
)

router = APIRouter(prefix="/delivery", tags=["delivery"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_unit_of_work(request: Request):
    """One unit of work per request, built here and shared with nothing else.

    FastAPI caches a dependency's value for the lifetime of a single request, so every
    service below joins *this* request's transaction and no other's. Returning a
    long-lived instance instead was the defect this replaced.
    """
    factory = _state(request, "unit_of_work_factory", "persistence")
    return factory()


UnitOfWork = Annotated[object, Depends(get_unit_of_work)]


def get_doorstep(request: Request, unit_of_work: UnitOfWork) -> DoorstepService:
    state = request.app.state
    return DoorstepService(
        unit_of_work,
        code_policy=state.code_policy,
        id_policy=state.id_policy,
        delivery_code_key=state.settings.delivery_code_hmac_key or "",
    )


def get_payments(unit_of_work: UnitOfWork) -> PaymentService:
    return PaymentService(unit_of_work)


def get_outcomes(
    request: Request,
    unit_of_work: UnitOfWork,
    doorstep: Doorstep,
    payments: Payments,
) -> OutcomeService:
    # The doorstep and payment services share this request's unit of work, because
    # FastAPI resolves each dependency once per request.
    return OutcomeService(
        unit_of_work,
        payment_service=payments,
        doorstep_service=doorstep,
        outbox_max_attempts=request.app.state.settings.outbox_max_attempts,
    )


def get_receiver(unit_of_work: UnitOfWork) -> ReceiverService:
    return ReceiverService(unit_of_work)


def get_authorizer(request: Request) -> DeliveryAuthorizer:
    return _state(request, "authorizer", "authorizer")


Doorstep = Annotated[DoorstepService, Depends(get_doorstep)]
Payments = Annotated[PaymentService, Depends(get_payments)]
Outcomes = Annotated[OutcomeService, Depends(get_outcomes)]
Receiver = Annotated[ReceiverService, Depends(get_receiver)]
Authorizer = Annotated[DeliveryAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *, authorizer: DeliveryAuthorizer, header: str | None, command: DeliveryCommand
) -> DeliveryActor:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_bearer_token"})
    token = header.split(" ", 1)[1].strip()
    try:
        decision = await authorizer.authorize(bearer_token=token, command=command)
    except AuthorizerUnavailableError:
        # Identity being unreachable is not a statement about this caller.
        raise HTTPException(
            status_code=503, detail={"code": "authorization_unavailable"}
        ) from None
    if decision.outcome is AuthorizationOutcome.UNAUTHENTICATED:
        raise HTTPException(status_code=401, detail={"code": "unauthenticated"})
    if decision.outcome is not AuthorizationOutcome.ALLOWED or decision.actor is None:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return decision.actor


# ------------------------------------------------------------------ manifest


@router.post("/manifests", response_model=ManifestResponse, status_code=201)
async def open_manifest(
    payload: OpenManifestRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ManifestResponse:
    """v6.3 p.25 — the driver builds their round at the destination hub."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.MANIFEST_BUILD,
    )
    with _domain_errors():
        manifest = doorstep.open_manifest(
            driver_principal_id=actor.principal_id, hub_id=payload.hub_id
        )
    return _manifest_response(manifest)


@router.post(
    "/manifests/{manifest_id}/parcels", response_model=StopResponse, status_code=201
)
async def scan_parcel(
    manifest_id: UUID,
    payload: ScanParcelRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    """The scan that transfers custody (v6.3 p.26).

    The delivery code, if supplied, is hashed against this stop and discarded here.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.MANIFEST_BUILD,
    )
    with _domain_errors():
        manifest = doorstep.manifest(manifest_id)
        if manifest.driver_principal_id != actor.principal_id:
            raise HTTPException(status_code=403, detail={"code": "forbidden"})
        stop = doorstep.scan_onto_manifest(
            manifest_id=manifest_id,
            parcel=ParcelForManifest(
                tracking_code=payload.tracking_code,
                settings=_settings(payload.settings),
                delivery_code=payload.delivery_code,
                named_receiver=payload.named_receiver,
                cod_amount=_money(payload.cod_amount),
                payment_method_expected=payload.payment_method_expected,
            ),
        )
    return _stop_response(stop)


@router.get("/stops", response_model=list[StopResponse])
async def list_my_stops(
    doorstep: Doorstep,
    payments: Payments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[StopResponse]:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_READ
    )
    with _domain_errors():
        stops = doorstep.list_for_driver(driver_principal_id=actor.principal_id)
    # `doorstep` is passed so a waiting stop still reports its remaining seconds:
    # the work list shows the countdown, and a client that had to compute one
    # from `wait_started_at` would be running the wait on its own clock.
    # `payments` so a stop already paid for does not look unpaid in the list.
    return [
        _stop_response(stop, doorstep=doorstep, payments=payments) for stop in stops
    ]


@router.get("/stops/{stop_id}", response_model=StopResponse)
async def read_stop(
    stop_id: UUID,
    doorstep: Doorstep,
    payments: Payments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_READ
    )
    with _domain_errors():
        stop = doorstep.get_stop(stop_id)
        _assert_is_the_carrying_driver(stop, actor)
    # Reading the stop is how a client recovers its state after a restart or a
    # resume: the same remaining seconds `POST /wait` gave, and whatever was
    # already collected at this door.
    return _stop_response(stop, doorstep=doorstep, payments=payments)


# ------------------------------------------------------------------ the round


@router.post("/stops/{stop_id}/depart", response_model=StopResponse)
async def depart(
    stop_id: UUID,
    payload: DepartRequest,
    doorstep: Doorstep,
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    """DRV-L01, NTF-07 — the receiver is told, with an ETA, before the driver sets off."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_DEPART
    )
    with _domain_errors():
        outcomes.announce_departure(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            eta_from_minutes=payload.eta_from_minutes,
            eta_to_minutes=payload.eta_to_minutes,
        )
        stop = doorstep.get_stop(stop_id)
    return _stop_response(stop)


@router.post("/stops/{stop_id}/arrive", response_model=StopResponse)
async def arrive(
    stop_id: UUID,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_ARRIVE
    )
    with _domain_errors():
        stop = doorstep.record_arrival(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return _stop_response(stop, doorstep=doorstep)


@router.post("/stops/{stop_id}/wait", response_model=StopResponse)
async def start_wait(
    stop_id: UUID,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    """v6.3 p.26 — the ten minutes start here, and only the clock ends them."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_WAIT
    )
    with _domain_errors():
        stop = doorstep.start_wait(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return _stop_response(stop, doorstep=doorstep)


# ------------------------------------------------------------------ the door


@router.get("/code-policy", response_model=DeliveryCodePolicyResponse)
async def read_code_policy(
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DeliveryCodePolicyResponse:
    """DRV-L05 — the app asks how many boxes to draw, and may be told "undecided"."""
    await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_READ
    )
    policy = doorstep.code_policy
    return DeliveryCodePolicyResponse(
        decided=policy.is_decided,
        length=policy.length,
        conflicting_lengths=list(CONFLICTING_LENGTHS),
        conflict_sources={str(k): v for k, v in CONFLICT_SOURCES.items()},
    )


@router.post("/stops/{stop_id}/verify/code", response_model=VerificationResponse)
async def verify_with_code(
    stop_id: UUID,
    payload: VerifyWithCodeRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> VerificationResponse:
    """v6.3 p.26 — anyone holding the code may receive the parcel.

    The code is never echoed, logged, or stored: it is compared as a keyed digest.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_VERIFY
    )
    with _domain_errors():
        result = doorstep.verify_with_code(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            code=payload.code,
        )
    return VerificationResponse(
        stop=_stop_response(result.stop),
        method=result.attempt.method,
        outcome=result.attempt.outcome,
        attempts_used=result.stop.code_attempt_count,
    )


@router.post("/stops/{stop_id}/verify/id", response_model=VerificationResponse)
async def verify_with_id(
    stop_id: UUID,
    payload: VerifyWithIdRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> VerificationResponse:
    """The fallback, and only for the receiver the merchant named (v6.3 p.26).

    The name is compared here and not stored; there is no field for a photograph of the
    document, and none is accepted.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_VERIFY
    )
    with _domain_errors():
        stop = doorstep.get_stop(stop_id)
        _assert_is_the_carrying_driver(stop, actor)
        matches = (
            stop.named_receiver is not None
            and _normalise(stop.named_receiver) == _normalise(payload.presented_name)
        )
        result = doorstep.verify_with_named_receiver_id(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            id_matches_named_receiver=matches,
        )
    return VerificationResponse(
        stop=_stop_response(result.stop),
        method=result.attempt.method,
        outcome=result.attempt.outcome,
        attempts_used=result.stop.code_attempt_count,
    )


@router.post("/stops/{stop_id}/seal", response_model=StopResponse)
async def check_seal(
    stop_id: UUID,
    payload: SealCheckRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    """The merchant's parcel seal, not the hub's batch seal (v6.3 p.14)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.STOP_SEAL_CHECK,
    )
    with _domain_errors():
        stop = doorstep.check_parcel_seal(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            intact=payload.outcome is SealCheckOutcome.INTACT,
        )
    return _stop_response(stop)


@router.post("/stops/{stop_id}/inspection", response_model=StopResponse)
async def record_inspection(
    stop_id: UUID,
    payload: InspectionRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> StopResponse:
    """Open-box only when the merchant enabled it (v6.3 p.35, p.36)."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_INSPECT
    )
    with _domain_errors():
        stop = doorstep.record_inspection(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            outcome=payload.outcome,
        )
    return _stop_response(stop)


@router.post("/stops/{stop_id}/photos", response_model=PhotoResponse, status_code=201)
async def attach_photo(
    stop_id: UUID,
    payload: AttachPhotoRequest,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PhotoResponse:
    """MER-11 — a pointer to stored evidence; Delivery never holds the bytes."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_PHOTO
    )
    with _domain_errors():
        photo = doorstep.attach_photo(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            stage=payload.stage,
            media=_media(payload.media),
        )
    return _photo_response(photo)


# ------------------------------------------------------------------ payment


@router.post("/stops/{stop_id}/payment/prepaid", response_model=PaymentResponse)
async def confirm_prepaid(
    stop_id: UUID,
    payload: ConfirmPrepaidRequest,
    payments: Payments,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PaymentResponse:
    _ = payload
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_PAY
    )
    with _domain_errors():
        _assert_is_the_carrying_driver(doorstep.get_stop(stop_id), actor)
        result = payments.confirm_prepaid(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return _payment_response(result.payment)


@router.post("/stops/{stop_id}/payment/cash", response_model=PaymentResponse)
async def collect_cash(
    stop_id: UUID,
    payload: CollectCashRequest,
    payments: Payments,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PaymentResponse:
    """Cash enters the driver's custody and must be settled before the shift ends."""
    _ = payload
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_PAY
    )
    with _domain_errors():
        _assert_is_the_carrying_driver(doorstep.get_stop(stop_id), actor)
        result = payments.collect_cash(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return _payment_response(result.payment)


@router.post("/stops/{stop_id}/payment/card/approved", response_model=PaymentResponse)
async def record_card_approval(
    stop_id: UUID,
    payload: CardApprovalRequest,
    payments: Payments,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PaymentResponse:
    """DRV-L14, DRV-L15 — proof before handover, and never into driver cash custody."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_PAY
    )
    with _domain_errors():
        _assert_is_the_carrying_driver(doorstep.get_stop(stop_id), actor)
        result = payments.record_card_approval(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            pos_reference=payload.pos_reference,
            pos_receipt=_media(payload.pos_receipt),
        )
    return _payment_response(result.payment)


@router.post("/stops/{stop_id}/payment/card/declined", response_model=PaymentResponse)
async def record_card_decline(
    stop_id: UUID,
    payload: CardDeclineRequest,
    payments: Payments,
    doorstep: Doorstep,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PaymentResponse:
    """DRV-L13 — the parcel is still at the door and still payable in cash."""
    _ = payload
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_PAY
    )
    with _domain_errors():
        _assert_is_the_carrying_driver(doorstep.get_stop(stop_id), actor)
        payment = payments.record_card_decline(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return _payment_response(payment)


# ------------------------------------------------------------------ outcomes


@router.post("/stops/{stop_id}/deliver", response_model=DeliveryResponse)
async def complete_delivery(
    stop_id: UUID,
    outcomes: Outcomes,
    payments: Payments,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DeliveryResponse:
    """The handover. A COD parcel unpaid at this point is refused (v6.3 p.31)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.STOP_COMPLETE,
    )
    with _domain_errors():
        result = outcomes.complete_delivery(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
        payment = payments.payment_for(stop_id=stop_id)
    return DeliveryResponse(
        stop=_stop_response(result.stop),
        payment=_payment_response(payment) if payment is not None else None,
    )


@router.post("/stops/{stop_id}/refuse", response_model=FailureResponse)
async def record_refusal(
    stop_id: UUID,
    payload: RefusalRequest,
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> FailureResponse:
    """v6.3 p.34 — nothing is collected and the parcel stays in HUDHUD custody."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_FAIL
    )
    with _domain_errors():
        result = outcomes.record_refusal(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            reason=payload.reason,
        )
    return FailureResponse(
        stop=_stop_response(result.stop), attempt=_attempt_response(result.attempt)
    )


@router.post("/stops/{stop_id}/absent", response_model=FailureResponse)
async def record_absence(
    stop_id: UUID,
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> FailureResponse:
    """v6.3 p.26 — recordable only once the ten minutes are up."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_FAIL
    )
    with _domain_errors():
        result = outcomes.record_absence(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return FailureResponse(
        stop=_stop_response(result.stop), attempt=_attempt_response(result.attempt)
    )


@router.post("/stops/{stop_id}/verification-failed", response_model=FailureResponse)
async def record_verification_failure(
    stop_id: UUID,
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> FailureResponse:
    """DRV-L08 — verification could not be completed; the parcel stays with HUDHUD."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_FAIL
    )
    with _domain_errors():
        result = outcomes.record_verification_failure(
            stop_id=stop_id, driver_principal_id=actor.principal_id
        )
    return FailureResponse(
        stop=_stop_response(result.stop), attempt=_attempt_response(result.attempt)
    )


@router.get("/failed-attempts", response_model=list[FailedAttemptResponse])
async def list_attempts_awaiting_operations(
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[FailedAttemptResponse]:
    """OPS-08 — the operations queue of parcels held for a next attempt."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.NEXT_ATTEMPT_DECIDE,
    )
    if not actor.may_decide_next_attempt:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        attempts = outcomes.attempts_awaiting_operations()
    return [_attempt_response(attempt) for attempt in attempts]


@router.post(
    "/failed-attempts/{attempt_id}/next-attempt", response_model=FailedAttemptResponse
)
async def decide_next_attempt(
    attempt_id: UUID,
    payload: DecideNextAttemptRequest,
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> FailedAttemptResponse:
    """OPS-08 — "Held for next attempt — decided by operations"."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.NEXT_ATTEMPT_DECIDE,
    )
    with _domain_errors():
        attempt = outcomes.decide_next_attempt(
            attempt_id=attempt_id,
            decision=payload.decision,
            decider_principal_id=actor.principal_id,
            actor_is_operations=actor.may_decide_next_attempt,
        )
    return _attempt_response(attempt)


@router.get("/failed-attempts/past-hold", response_model=list[FailedAttemptResponse])
async def list_attempts_past_their_hold(
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[FailedAttemptResponse]:
    """DRV-L20 — parcels whose three-day hold has run out (v6.3 p.29)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.NEXT_ATTEMPT_DECIDE,
    )
    if not actor.may_decide_next_attempt:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        attempts = outcomes.attempts_past_their_hold()
    return [_attempt_response(attempt) for attempt in attempts]


@router.post("/failed-attempts/return-past-hold", response_model=ReturnedAfterHoldResponse)
async def return_parcels_past_their_hold(
    outcomes: Outcomes,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ReturnedAfterHoldResponse:
    """v6.3 p.29 — "still undelivered after the hold ⇒ returned to the merchant".

    Operations runs this; the returning actor is recorded on every parcel it moves, so
    a return is attributable in the same way a hand-made decision is.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.NEXT_ATTEMPT_DECIDE,
    )
    if not actor.may_decide_next_attempt:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        returned = outcomes.return_parcels_past_their_hold(
            actor_principal_id=actor.principal_id
        )
    return ReturnedAfterHoldResponse(
        hold_days=HOLD_DAYS,
        returned=[_attempt_response(attempt) for attempt in returned],
    )


@router.post(
    "/stops/{stop_id}/incident", response_model=IssueReportResponse, status_code=201
)
async def raise_driver_incident(
    stop_id: UUID,
    payload: RaiseIncidentRequest,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> IssueReportResponse:
    """DRV-L21 — Driver App v8 `lmIncident`, opened from the stop.

    The visit still has to end one of the three ways; this only records that something
    happened to the parcel while the driver had it.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.RECEIVER_ISSUE_REPORT,
    )
    with _domain_errors():
        report = receiver.raise_driver_incident(
            stop_id=stop_id,
            driver_principal_id=actor.principal_id,
            kind=payload.kind,
            detail=payload.detail,
            media=tuple(_media(item) for item in payload.media),
        )
    return _issue_response(report)


# ------------------------------------------------------------------ receiver


@router.put(
    "/parcels/{tracking_code}/handover-preference",
    response_model=HandoverPreferenceResponse,
)
async def set_handover_preference(
    tracking_code: str,
    payload: HandoverPreferenceRequest,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> HandoverPreferenceResponse:
    """CUS-11 — a stated window and an exact pin mean fewer failed attempts (p.20)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.RECEIVER_PREFERENCE_SET,
    )
    with _domain_errors():
        try:
            preference = receiver.set_handover_preference(
                tracking_code=tracking_code,
                principal_id=actor.principal_id,
                window=_window(payload.window),
                address_line=payload.address_line,
                landmark=payload.landmark,
                geo=_geo(payload.geo),
            )
        except ValueError:
            raise HTTPException(
                status_code=422, detail={"code": "empty_handover_preference"}
            ) from None
    return _preference_response(preference)


@router.get("/parcels/{tracking_code}/arrival", response_model=ArrivalViewResponse)
async def read_arrival_view(
    tracking_code: str,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ArrivalViewResponse:
    """What the receiver sees while the parcel is coming. No code, no driver number."""
    await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.STOP_READ
    )
    with _domain_errors():
        view = receiver.arrival_view(tracking_code=tracking_code)
    return _arrival_response(view)


@router.post(
    "/parcels/{tracking_code}/issues",
    response_model=IssueReportResponse,
    status_code=201,
)
async def report_issue(
    tracking_code: str,
    payload: ReportIssueRequest,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> IssueReportResponse:
    """CUS-12 — Delivery records the report; whether it becomes a claim is Claims'."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.RECEIVER_ISSUE_REPORT,
    )
    with _domain_errors():
        report = receiver.report_issue(
            tracking_code=tracking_code,
            kind=payload.kind,
            detail=payload.detail,
            principal_id=actor.principal_id,
            media=tuple(_media(item) for item in payload.media),
        )
    return _issue_response(report)


@router.post(
    "/parcels/{tracking_code}/rating",
    response_model=RatingAcknowledgementResponse,
    status_code=201,
)
async def rate_courier(
    tracking_code: str,
    payload: RateCourierRequest,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> RatingAcknowledgementResponse:
    """CUS-13, SEC-08 — "Your rating stays private"."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=DeliveryCommand.COURIER_RATE
    )
    with _domain_errors():
        rating = receiver.rate_courier(
            tracking_code=tracking_code,
            score=payload.score,
            tags=tuple(payload.tags),
            note=payload.note,
            principal_id=actor.principal_id,
        )
    return _rating_response(rating)


@router.get(
    "/couriers/{courier_principal_id}/rating-summary",
    response_model=CourierRatingSummaryResponse,
)
async def read_rating_summary(
    courier_principal_id: UUID,
    receiver: Receiver,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CourierRatingSummaryResponse:
    """SEC-08 — a courier may see their own aggregate, never a rater and never a note."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=DeliveryCommand.COURIER_RATING_READ,
    )
    if actor.principal_id != courier_principal_id and not (
        actor.is_operations or actor.is_support
    ):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    with _domain_errors():
        summary = receiver.rating_summary_for_courier(
            courier_principal_id=courier_principal_id
        )
    return _summary_response(summary)


# ------------------------------------------------------------------ helpers


class _domain_errors:  # noqa: N801 - a context manager used as a statement
    """Translate domain errors into HTTP answers at one place."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None or isinstance(exc, HTTPException):
            return False
        if isinstance(exc, DeliveryError) or type(exc).__name__ in {
            "DeliveryCodeLengthNotDecided",
            "IdPhotoRetentionNotDecided",
        }:
            raise_http_for_domain_error(exc)
        return False


def _assert_is_the_carrying_driver(stop: DeliveryStop, actor: DeliveryActor) -> None:
    if stop.driver_principal_id != actor.principal_id and not (
        actor.is_operations or actor.is_support
    ):
        raise NotThisDriversStop(stop.tracking_code)


def _normalise(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def _settings(model: ParcelSettingsModel) -> ParcelSettings:
    return ParcelSettings(
        open_box_allowed=model.open_box_allowed,
        photo_documentation=model.photo_documentation,
        packaging_seal_code=model.packaging_seal_code,
    )


def _money(model: MoneyModel | None) -> Money | None:
    if model is None:
        return None
    return Money(minor_units=model.minor_units, currency=Currency(model.currency))


def _money_model(amount: Money | None) -> MoneyModel | None:
    if amount is None:
        return None
    return MoneyModel(minor_units=amount.minor_units, currency=amount.currency.value)


def _media(model: MediaRefModel | None) -> EvidenceMediaRef | None:
    if model is None:
        return None
    return EvidenceMediaRef(
        bucket=model.bucket, key=model.key, content_type=model.content_type
    )


def _window(model: TimeWindowModel | None) -> TimeWindow | None:
    if model is None:
        return None
    return TimeWindow(
        starts_at_hour=model.starts_at_hour, ends_at_hour=model.ends_at_hour
    )


def _geo(model: GeoPointModel | None) -> GeoPoint | None:
    if model is None:
        return None
    return GeoPoint(latitude=model.latitude, longitude=model.longitude)


def _manifest_response(manifest: DeliveryManifest) -> ManifestResponse:
    return ManifestResponse(
        manifest_id=manifest.manifest_id,
        driver_principal_id=manifest.driver_principal_id,
        hub_id=manifest.hub_id,
        created_at=manifest.created_at,
        closed_at=manifest.closed_at,
        is_open=manifest.is_open,
    )


def _stop_response(
    stop: DeliveryStop,
    *,
    doorstep: DoorstepService | None = None,
    payments: PaymentService | None = None,
) -> StopResponse:
    remaining = None
    if doorstep is not None and stop.wait_started_at is not None:
        remaining = doorstep.wait_remaining_seconds(stop_id=stop.stop_id)
    # Only the read paths pass `payments`: a command's own response already
    # carries its result, and a driver re-reading a stop is the case that needs
    # to know money was taken before the app restarted.
    payment = None
    if payments is not None:
        record = payments.payment_for(stop_id=stop.stop_id)
        if record is not None:
            payment = _payment_response(record)
    return StopResponse(
        stop_id=stop.stop_id,
        manifest_id=stop.manifest_id,
        tracking_code=stop.tracking_code,
        driver_principal_id=stop.driver_principal_id,
        status=stop.status,
        settings=ParcelSettingsModel(
            open_box_allowed=stop.settings.open_box_allowed,
            photo_documentation=stop.settings.photo_documentation,
            packaging_seal_code=stop.settings.packaging_seal_code,
        ),
        # Never the code and never the digest: only whether one exists.
        has_delivery_code=stop.delivery_code_digest is not None,
        named_receiver=stop.named_receiver,
        cod_amount=_money_model(stop.cod_amount),
        payment_method_expected=stop.payment_method_expected,
        requires_payment_at_door=stop.requires_payment_at_door,
        requires_seal_check=stop.requires_seal_check,
        custody_taken_at=stop.custody_taken_at,
        departed_at=stop.departed_at,
        arrived_at=stop.arrived_at,
        wait_started_at=stop.wait_started_at,
        wait_remaining_seconds=remaining,
        verified_at=stop.verified_at,
        verified_by_method=stop.verified_by_method,
        seal_outcome=stop.seal_outcome,
        inspection_outcome=stop.inspection_outcome,
        delivered_at=stop.delivered_at,
        failure_reason=stop.failure_reason,
        refusal_reason=stop.refusal_reason,
        closed_at=stop.closed_at,
        code_attempt_count=stop.code_attempt_count,
        payment=payment,
        version=stop.version,
    )


def _photo_response(photo: PhotoEvidence) -> PhotoResponse:
    return PhotoResponse(
        photo_id=photo.photo_id,
        stop_id=photo.stop_id,
        stage=photo.stage,
        media=MediaRefModel(
            bucket=photo.media.bucket,
            key=photo.media.key,
            content_type=photo.media.content_type,
        ),
        captured_at=photo.captured_at,
    )


def _payment_response(payment: PaymentRecord) -> PaymentResponse:
    return PaymentResponse(
        payment_id=payment.payment_id,
        stop_id=payment.stop_id,
        method=payment.method,
        outcome=payment.outcome.value,
        amount=_money_model(payment.amount),
        pos_reference=payment.pos_reference,
        has_pos_receipt=payment.pos_receipt is not None,
        enters_driver_cash_custody=payment.enters_driver_cash_custody,
        recorded_at=payment.recorded_at,
    )


def _attempt_response(attempt: FailedAttempt) -> FailedAttemptResponse:
    return FailedAttemptResponse(
        attempt_id=attempt.attempt_id,
        stop_id=attempt.stop_id,
        tracking_code=attempt.tracking_code,
        reason=attempt.reason,
        recorded_at=attempt.recorded_at,
        next_attempt_decision=attempt.next_attempt_decision,
        decided_at=attempt.decided_at,
        awaits_operations=attempt.awaits_operations,
        hold_expires_at=attempt.hold_expires_at(),
    )


def _preference_response(preference: ReceiverPreference) -> HandoverPreferenceResponse:
    return HandoverPreferenceResponse(
        preference_id=preference.preference_id,
        tracking_code=preference.tracking_code,
        window=(
            TimeWindowModel(
                starts_at_hour=preference.window.starts_at_hour,
                ends_at_hour=preference.window.ends_at_hour,
            )
            if preference.window is not None
            else None
        ),
        address_line=preference.address_line,
        landmark=preference.landmark,
        geo=(
            GeoPointModel(
                latitude=preference.geo.latitude, longitude=preference.geo.longitude
            )
            if preference.geo is not None
            else None
        ),
        has_exact_location=preference.has_exact_location,
        updated_at=preference.updated_at,
    )


def _issue_response(report: ParcelIssueReport) -> IssueReportResponse:
    return IssueReportResponse(
        report_id=report.report_id,
        tracking_code=report.tracking_code,
        kind=report.kind,
        source=report.source,
        stop_id=report.stop_id,
        detail=report.detail,
        media_count=len(report.media),
        reported_at=report.reported_at,
    )


def _rating_response(rating: CourierRating) -> RatingAcknowledgementResponse:
    return RatingAcknowledgementResponse(
        rating_id=rating.rating_id,
        tracking_code=rating.tracking_code,
        score=rating.score,
        tags=list(rating.tags),
        rated_at=rating.rated_at,
    )


def _arrival_response(view: ArrivalView) -> ArrivalViewResponse:
    return ArrivalViewResponse(
        tracking_code=view.tracking_code,
        status=view.status,
        departed_at=view.departed_at,
        wait_minutes_at_door=view.wait_minutes_at_door,
        preference=(
            _preference_response(view.preference) if view.preference is not None else None
        ),
    )


def _summary_response(summary: CourierRatingSummary) -> CourierRatingSummaryResponse:
    return CourierRatingSummaryResponse(
        courier_principal_id=summary.courier_principal_id,
        rating_count=summary.rating_count,
        average_score=summary.average_score,
        tag_counts=dict(summary.tag_counts),
    )
