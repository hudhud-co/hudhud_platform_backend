"""Claims HTTP adapter.

The claimant routes are scoped to the caller's own claims: the principal comes from the
authorization decision, never from the path or the body, so nobody reads another
person's claim by changing a reference. A reference that is not yours answers 404 rather
than 403, because a 403 would confirm it is real.

**SEC-07 runs through the whole adapter.** A driver is never shown a compensation or
claim value: the driver-facing response models have no field for one, and the two models
that *can* carry an amount drop it for a driver, whatever else that driver is.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from claims.api.errors import raise_http_for_domain_error
from claims.api.schemas import (
    ApproveRequest,
    ClaimForClaimantResponse,
    ClaimForStaffResponse,
    ConversationResponse,
    DriverSummaryResponse,
    EvidenceRef,
    FileClaimRequest,
    FiledClaimResponse,
    HeldParcelsResponse,
    IncidentForStaffResponse,
    IncidentReportedResponse,
    MessageResponse,
    MoneyResponse,
    PostMessageRequest,
    RejectRequest,
    ReportIncidentRequest,
    ResolveIncidentRequest,
    ReturnsAndClaimsResponse,
    ReturnsAndClaimsRowResponse,
    StartReviewRequest,
)
from claims.application.claim_service import ClaimService
from claims.application.incident_service import IncidentService
from claims.domain.entities import (
    ClaimMessage,
    ClaimSummaryForDriver,
    CompensationClaim,
    DriverIncident,
)
from claims.domain.errors import ClaimsError
from claims.domain.money import Currency, Money
from claims.domain.value_objects import (
    ClaimOpenedBy,
    ClaimStatus,
    ConversationAuthor,
    EvidenceMediaRef,
)
from claims.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    ClaimsActor,
    ClaimsAuthorizer,
    ClaimsCommand,
)

router = APIRouter(prefix="/claims", tags=["claims"])

BearerHeader = Annotated[str | None, Header(alias="Authorization")]


def _state(request: Request, name: str, label: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail={"code": f"{label}_unavailable"})
    return value


def get_unit_of_work(request: Request):
    """One unit of work per request, built here and shared with nothing else.

    FastAPI caches a dependency's value for the lifetime of a single request, so both
    services below join *this* request's transaction and no other's. Storing a long-lived
    instance on ``app.state`` instead was the platform defect this service was built
    after; see `docs/audits/hudhud-app-redesign-v6.3/13-SHARED-UNIT-OF-WORK-P0.md`.
    """
    factory = _state(request, "unit_of_work_factory", "persistence")
    return factory()


UnitOfWork = Annotated[object, Depends(get_unit_of_work)]


def get_claims(unit_of_work: UnitOfWork) -> ClaimService:
    return ClaimService(unit_of_work)


def get_incidents(unit_of_work: UnitOfWork) -> IncidentService:
    return IncidentService(unit_of_work)


def get_authorizer(request: Request) -> ClaimsAuthorizer:
    return _state(request, "authorizer", "authorizer")


Claims = Annotated[ClaimService, Depends(get_claims)]
Incidents = Annotated[IncidentService, Depends(get_incidents)]
Authorizer = Annotated[ClaimsAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *,
    authorizer: ClaimsAuthorizer,
    header: str | None,
    command: ClaimsCommand,
) -> ClaimsActor:
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


def _opened_by(actor: ClaimsActor) -> ClaimOpenedBy:
    """CLM-02 — who is opening it, taken from the token rather than the body.

    A caller cannot claim to be the driver, and a driver cannot file as the sender: the
    three roles v6.3 p.42 names are read off the authenticated principal.
    """
    if actor.is_driver:
        return ClaimOpenedBy.DRIVER
    if actor.is_support or actor.is_operations:
        return ClaimOpenedBy.SUPPORT
    return ClaimOpenedBy.SENDER


def _media(refs: list[EvidenceRef]) -> tuple[EvidenceMediaRef, ...]:
    return tuple(
        EvidenceMediaRef(bucket=ref.bucket, key=ref.key, content_type=ref.content_type)
        for ref in refs
    )


# ------------------------------------------------------------------ claimant


@router.post("", response_model=FiledClaimResponse, status_code=201)
async def file_claim(
    payload: FileClaimRequest,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> FiledClaimResponse:
    """CLM-02, CLM-07 — the sender, the receiver or the driver opens a claim.

    ``sender_principal_id`` defaults to the caller, because the ordinary case is a sender
    filing about their own parcel. A receiver or driver filing names the sender, and
    CLM-01 compensates that person rather than whoever is on the phone.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_FILE
    )
    try:
        filed = claims.file_claim(
            tracking_code=payload.tracking_code,
            kind=payload.kind,
            opened_by=_opened_by(actor),
            opened_by_principal_id=actor.principal_id,
            sender_principal_id=payload.sender_principal_id or actor.principal_id,
            merchant_id=payload.merchant_id,
            description=payload.description,
            evidence=_media(payload.evidence),
            custody_boundary=payload.custody_boundary,
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    claim = filed.claim
    return FiledClaimResponse(
        reference=claim.reference,
        tracking_code=claim.tracking_code,
        kind=claim.kind,
        status=claim.status,
        submitted_at=claim.submitted_at,
    )


@router.get("", response_model=list[ClaimForClaimantResponse])
async def my_claims(
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ClaimForClaimantResponse]:
    """CLM-07 — the caller's own claims. There is no route to list anyone else's."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.CLAIM_READ_OWN,
    )
    found = claims.my_claims(principal_id=actor.principal_id)
    return [_claimant_response(claim, actor=actor) for claim in found]


# ------------------------------------------------------------------ driver
#
# Declared before `/{reference}`, because `/claims/incidents` would otherwise be read
# as a claim whose reference is the word "incidents".


@router.post("/incidents", response_model=IncidentReportedResponse, status_code=201)
async def report_incident(
    payload: ReportIncidentRequest,
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> IncidentReportedResponse:
    """DRV-P25 — a driver reporting what happened. No amount is taken or returned."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.INCIDENT_REPORT,
    )
    if not actor.is_driver:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    try:
        incident = incidents.report(
            kind=payload.kind,
            driver_principal_id=actor.principal_id,
            tracking_code=payload.tracking_code,
            note=payload.note,
            evidence=_media(payload.evidence),
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return IncidentReportedResponse(
        reference=incident.reference,
        kind=incident.kind.value,
        status=incident.status.value,
        tracking_code=incident.tracking_code,
        submitted_at=incident.reported_at,
        resolved_at=incident.resolved_at,
        parcel_stays_in_custody=incident.parcel_stays_in_custody,
    )


@router.get("/incidents", response_model=list[DriverSummaryResponse])
async def my_incidents(
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[DriverSummaryResponse]:
    """The driver's own reports, in the shape `incidentDone` shows."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.INCIDENT_READ_OWN,
    )
    found = incidents.my_incidents(driver_principal_id=actor.principal_id)
    return [_driver_response(summary) for summary in found]


@router.post(
    "/incidents/{reference}/investigation", response_model=IncidentForStaffResponse
)
async def start_investigation(
    reference: str,
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> IncidentForStaffResponse:
    """OPS-07 — operations picks it up."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.INCIDENT_RESOLVE,
    )
    try:
        incident = incidents.start_investigation(reference=reference, actor=actor)
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _incident_response(incident)


@router.post("/incidents/{reference}/resolution", response_model=IncidentForStaffResponse)
async def resolve_incident(
    reference: str,
    payload: ResolveIncidentRequest,
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> IncidentForStaffResponse:
    """OPS-07 — operations closes it, and says what was found."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.INCIDENT_RESOLVE,
    )
    try:
        incident = incidents.resolve(
            reference=reference,
            actor=actor,
            note=payload.note,
            linked_claim_id=payload.linked_claim_id,
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _incident_response(incident)


# ------------------------------------------------------------------ operations
#
# Also declared before `/{reference}`.


@router.get("/queue", response_model=list[ClaimForStaffResponse])
async def operations_queue(
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[ClaimForStaffResponse]:
    """Claims still waiting to be reviewed or decided."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.CLAIM_QUEUE_READ,
    )
    if not actor.may_review_a_claim:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return [_staff_response(claim, actor=actor) for claim in claims.awaiting_operations()]


@router.get("/returns-and-claims", response_model=ReturnsAndClaimsResponse)
async def returns_and_claims(
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ReturnsAndClaimsResponse:
    """OPS-06 — claims and driver incidents in one view.

    An operator who is also a driver sees the same rows without the amounts rather than
    being refused the view (SEC-07).
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.RETURNS_AND_CLAIMS_READ,
    )
    if not actor.may_see_the_returns_and_claims_view:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    rows = incidents.returns_and_claims(actor=actor)
    return ReturnsAndClaimsResponse(
        rows=[
            ReturnsAndClaimsRowResponse(
                reference=row.reference,
                tracking_code=row.tracking_code,
                kind=row.kind,
                status=row.status,
                opened_by=row.opened_by,
                submitted_at=row.submitted_at,
                awaiting_operations=row.awaiting_operations,
                compensation=_money(row.compensation_amount),
                extra=row.extra,
            )
            for row in rows
        ]
    )


@router.get("/held-parcels", response_model=HeldParcelsResponse)
async def held_parcels(
    incidents: Incidents,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> HeldParcelsResponse:
    """Parcels that must not move while a driver's report is open (DRV-P25)."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.RETURNS_AND_CLAIMS_READ,
    )
    if not actor.may_see_the_returns_and_claims_view:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    return HeldParcelsResponse(
        tracking_codes=list(incidents.parcels_held_by_open_incidents())
    )


# ------------------------------------------------------------------ CLM-06


@router.post("/returns/{tracking_code}", status_code=409)
async def request_return(
    tracking_code: str,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> None:
    """CLM-06 — there is no return window after acceptance at the door.

    The route exists so the absence is a stated decision with a reason attached, rather
    than a 404 somebody later "fixes". A receiver who accepted the parcel cannot change
    their mind; what they can still do is file a claim, which is a different question
    and is not blocked.
    """
    await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_FILE
    )
    try:
        claims.request_return(tracking_code=tracking_code)
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)


# ------------------------------------------------------------------ one claim


@router.get("/{reference}", response_model=ClaimForClaimantResponse)
async def read_claim(
    reference: str,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForClaimantResponse:
    """CLM-07 — the claimant's own claim. Someone else's answers 404."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.CLAIM_READ_OWN,
    )
    try:
        summary = claims.for_claimant(
            reference=reference, principal_id=actor.principal_id
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return ClaimForClaimantResponse(
        reference=summary.reference,
        tracking_code=summary.tracking_code,
        kind=summary.kind,
        status=summary.status,
        submitted_at=summary.submitted_at,
        decided_at=summary.decided_at,
        # SEC-07 — a claimant who is a driver sees their claim without the figure.
        compensation=(
            _money(summary.compensation_amount)
            if actor.may_see_a_compensation_value or not actor.is_driver
            else None
        ),
        rejection_reason=summary.rejection_reason,
        rejection_note=summary.rejection_note,
        message_count=summary.message_count,
    )


@router.post("/{reference}/withdrawal", response_model=ClaimForClaimantResponse)
async def withdraw_claim(
    reference: str,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForClaimantResponse:
    """A claimant withdrawing their own claim while it is still open."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=ClaimsCommand.CLAIM_WITHDRAW,
    )
    try:
        claim = claims.withdraw(reference=reference, principal_id=actor.principal_id)
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _claimant_response(claim, actor=actor)


@router.get("/{reference}/messages", response_model=ConversationResponse)
async def read_conversation(
    reference: str,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ConversationResponse:
    """CLM-07 — the claimant sees their own thread; support and operations see any."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_MESSAGE
    )
    try:
        messages = claims.conversation(
            reference=reference,
            principal_id=actor.principal_id,
            actor=actor,
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return ConversationResponse(
        reference=reference, messages=[_message_response(m) for m in messages]
    )


@router.post("/{reference}/messages", response_model=MessageResponse, status_code=201)
async def post_message(
    reference: str,
    payload: PostMessageRequest,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> MessageResponse:
    """A message on the claim's thread, from the claimant or from support.

    Who the author is comes from the token: support writes as support, and a claimant
    cannot post a message that appears to come from HUDHUD.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_MESSAGE
    )
    author = (
        ConversationAuthor.SUPPORT
        if actor.may_review_a_claim
        else ConversationAuthor.CLAIMANT
    )
    if author is ConversationAuthor.CLAIMANT:
        # A claimant may only write on their own thread. Reading it first gives the
        # same 404 a stranger's reference gives anywhere else in this adapter.
        try:
            claims.for_claimant(reference=reference, principal_id=actor.principal_id)
        except ClaimsError as exc:
            raise_http_for_domain_error(exc)
    try:
        message = claims.post_message(
            reference=reference,
            author=author,
            body=payload.body,
            author_principal_id=actor.principal_id,
            attachments=_media(payload.attachments),
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _message_response(message)


# ------------------------------------------------------------------ review


@router.post("/{reference}/review", response_model=ClaimForStaffResponse)
async def start_review(
    reference: str,
    payload: StartReviewRequest,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForStaffResponse:
    """CLM-03 — "Hudhud reviews scan and custody records before compensating"."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_REVIEW
    )
    try:
        claim = claims.start_review(
            reference=reference,
            actor=actor,
            custody_records_reviewed=payload.custody_records_reviewed,
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _staff_response(claim, actor=actor)


@router.post("/{reference}/custody-review", response_model=ClaimForStaffResponse)
async def record_custody_review(
    reference: str,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForStaffResponse:
    """The reviewer asserting that they read the scan and custody records."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_REVIEW
    )
    try:
        claim = claims.record_custody_review(reference=reference, actor=actor)
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _staff_response(claim, actor=actor)


@router.post("/{reference}/approval", response_model=ClaimForStaffResponse)
async def approve_claim(
    reference: str,
    payload: ApproveRequest,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForStaffResponse:
    """CLM-04, CLM-01 — approved, and the **sender** is compensated.

    Operations or an accountant, never support: reviewing is one thing and committing
    HUDHUD to a payment is another.
    """
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_DECIDE
    )
    try:
        claim = claims.approve(
            reference=reference,
            actor=actor,
            compensation=Money(
                payload.compensation_minor_units, Currency(payload.currency)
            ),
        )
    except ValueError as exc:
        # An unknown currency, or a negative amount the schema let through.
        raise HTTPException(
            status_code=422, detail={"code": "invalid_compensation_amount"}
        ) from exc
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _staff_response(claim, actor=actor)


@router.post("/{reference}/rejection", response_model=ClaimForStaffResponse)
async def reject_claim(
    reference: str,
    payload: RejectRequest,
    claims: Claims,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> ClaimForStaffResponse:
    """CLM-04 — "rejected ⇒ documented reason given"."""
    actor = await _authorize(
        authorizer=authorizer, header=authorization, command=ClaimsCommand.CLAIM_DECIDE
    )
    try:
        claim = claims.reject(
            reference=reference, actor=actor, reason=payload.reason, note=payload.note
        )
    except ClaimsError as exc:
        raise_http_for_domain_error(exc)
    return _staff_response(claim, actor=actor)


# ------------------------------------------------------------------ responses


def _money(amount: Money | None) -> MoneyResponse | None:
    if amount is None:
        return None
    return MoneyResponse(
        minor_units=amount.minor_units, currency=amount.currency.value
    )


def _claimant_response(
    claim: CompensationClaim, *, actor: ClaimsActor
) -> ClaimForClaimantResponse:
    show_amount = not actor.is_driver and claim.status is ClaimStatus.APPROVED
    return ClaimForClaimantResponse(
        reference=claim.reference,
        tracking_code=claim.tracking_code,
        kind=claim.kind,
        status=claim.status,
        submitted_at=claim.submitted_at,
        decided_at=claim.decided_at,
        compensation=_money(claim.compensation_amount) if show_amount else None,
        rejection_reason=claim.rejection_reason,
        rejection_note=claim.rejection_note,
    )


def _staff_response(
    claim: CompensationClaim, *, actor: ClaimsActor
) -> ClaimForStaffResponse:
    return ClaimForStaffResponse(
        reference=claim.reference,
        tracking_code=claim.tracking_code,
        kind=claim.kind,
        opened_by=claim.opened_by,
        status=claim.status,
        custody_boundary=claim.custody_boundary,
        custody_records_reviewed=claim.custody_records_reviewed,
        submitted_at=claim.submitted_at,
        decided_at=claim.decided_at,
        # SEC-07 — an operator who is also a driver sees the claim without the figure.
        compensation=(
            _money(claim.compensation_amount)
            if actor.may_see_a_compensation_value
            else None
        ),
        rejection_reason=claim.rejection_reason,
        rejection_note=claim.rejection_note,
        version=claim.version,
    )


def _incident_response(incident: DriverIncident) -> IncidentForStaffResponse:
    return IncidentForStaffResponse(
        reference=incident.reference,
        kind=incident.kind,
        status=incident.status.value,
        tracking_code=incident.tracking_code,
        reported_at=incident.reported_at,
        note=incident.note,
        resolved_at=incident.resolved_at,
        resolution_note=incident.resolution_note,
        linked_claim_id=incident.linked_claim_id,
        parcel_stays_in_custody=incident.parcel_stays_in_custody,
        version=incident.version,
    )


def _driver_response(summary: ClaimSummaryForDriver) -> DriverSummaryResponse:
    return DriverSummaryResponse(
        reference=summary.reference,
        kind=summary.kind,
        status=summary.status,
        tracking_code=summary.tracking_code,
        submitted_at=summary.submitted_at,
        resolved_at=summary.resolved_at,
    )


def _message_response(message: ClaimMessage) -> MessageResponse:
    return MessageResponse(
        message_id=message.message_id,
        author=message.author,
        body=message.body,
        written_at=message.written_at,
        attachments=[
            EvidenceRef(
                bucket=ref.bucket, key=ref.key, content_type=ref.content_type
            )
            for ref in message.attachments
        ],
    )
