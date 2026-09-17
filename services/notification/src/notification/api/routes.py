"""Notification HTTP adapter.

Almost every route here is scoped to the caller's own data: their preferences, their own
notification centre. The principal comes from the authorization decision, never from the
path, so one person can never read another's history by changing an id.

No response on this adapter carries a message body, a phone number or a delivery code.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from notification.api.errors import raise_http_for_domain_error
from notification.api.schemas import (
    BulletinRequest,
    BulletinResponse,
    CentreEntryResponse,
    CentreResponse,
    DeliveryStatusResponse,
    NotificationSummary,
    PreferenceResponse,
    SetPreferenceRequest,
)
from notification.application.dispatch_service import DispatchService
from notification.application.fan_out_service import FanOutService, RecipientRef
from notification.application.preference_service import PreferenceService
from notification.domain.entities import CentreEntry, ChannelPreference, Notification
from notification.domain.errors import NotificationError
from notification.domain.value_objects import (
    Audience,
    Category,
    Channel,
    is_mandatory,
)
from notification.ports.authorization import (
    AuthorizationOutcome,
    AuthorizerUnavailableError,
    NotificationActor,
    NotificationAuthorizer,
    NotificationCommand,
)

router = APIRouter(prefix="/notification", tags=["notification"])

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


def get_preferences(unit_of_work: UnitOfWork) -> PreferenceService:
    return PreferenceService(unit_of_work)


def get_fan_out(request: Request, unit_of_work: UnitOfWork) -> FanOutService:
    return FanOutService(
        unit_of_work,
        recipient_key=request.app.state.settings.recipient_hash_key or "",
    )


def get_dispatch(request: Request, unit_of_work: UnitOfWork) -> DispatchService:
    state = request.app.state
    return DispatchService(
        unit_of_work,
        transports=state.transports,
        templates=state.templates,
    )


def get_authorizer(request: Request) -> NotificationAuthorizer:
    return _state(request, "authorizer", "authorizer")


Preferences = Annotated[PreferenceService, Depends(get_preferences)]
FanOut = Annotated[FanOutService, Depends(get_fan_out)]
Dispatch = Annotated[DispatchService, Depends(get_dispatch)]
Authorizer = Annotated[NotificationAuthorizer, Depends(get_authorizer)]


async def _authorize(
    *,
    authorizer: NotificationAuthorizer,
    header: str | None,
    command: NotificationCommand,
) -> NotificationActor:
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


# ------------------------------------------------------------------ preferences


@router.get("/preferences", response_model=list[PreferenceResponse])
async def list_preferences(
    preferences: Preferences,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> list[PreferenceResponse]:
    """The caller's own preferences. There is no route to read anyone else's."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.PREFERENCES_READ,
    )
    return [
        _preference_response(item)
        for item in preferences.list_preferences(principal_id=actor.principal_id)
    ]


@router.put("/preferences", response_model=PreferenceResponse)
async def set_preference(
    payload: SetPreferenceRequest,
    preferences: Preferences,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> PreferenceResponse:
    """NTF-10 — per category and channel. The acceptance SMS cannot be switched off."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.PREFERENCES_UPDATE,
    )
    try:
        preference = preferences.set_preference(
            principal_id=actor.principal_id,
            category=Category(payload.category),
            channel=Channel(payload.channel),
            enabled=payload.enabled,
        )
    except NotificationError as exc:
        raise_http_for_domain_error(exc)
    return _preference_response(preference)


# ------------------------------------------------------------------ centre


@router.get("/centre", response_model=CentreResponse)
async def read_centre(
    preferences: Preferences,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CentreResponse:
    """CUS-14, NTF-09 — the caller's own notification centre, newest first."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.CENTRE_READ,
    )
    entries = preferences.list_centre(principal_id=actor.principal_id)
    return CentreResponse(
        unread=sum(1 for entry in entries if not entry.is_read),
        entries=[_entry_response(entry) for entry in entries],
    )


@router.post("/centre/{entry_id}/read", response_model=CentreEntryResponse)
async def mark_entry_read(
    entry_id: UUID,
    preferences: Preferences,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> CentreEntryResponse:
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.CENTRE_MARK_READ,
    )
    try:
        entry = preferences.mark_read(
            entry_id=entry_id, principal_id=actor.principal_id
        )
    except NotificationError as exc:
        raise_http_for_domain_error(exc)
    return _entry_response(entry)


# ------------------------------------------------------------------ bulletin


@router.post("/bulletins", response_model=BulletinResponse, status_code=201)
async def send_bulletin(
    payload: BulletinRequest,
    fan_out: FanOut,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> BulletinResponse:
    """NTF-09 — Operations reaching drivers. Operations only."""
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.BULLETIN_SEND,
    )
    if not actor.is_operations:
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    principals = list(payload.driver_principal_ids)
    recipients = tuple(
        RecipientRef(
            phone=phone,
            audience=Audience.DRIVER,
            principal_id=principals[index] if index < len(principals) else None,
            app_installed=True,
        )
        for index, phone in enumerate(payload.driver_phones)
    )
    try:
        result = fan_out.fan_out(
            category=Category.OPERATIONS_BULLETIN,
            trigger_reference=payload.reference,
            recipients=recipients,
            context={"summary": payload.summary},
        )
    except NotificationError as exc:
        raise_http_for_domain_error(exc)
    return BulletinResponse(
        planned=len(result.plan.planned),
        suppressed=len(result.plan.suppressed),
        unreachable=len(result.plan.unreachable),
    )


# ------------------------------------------------------------------ operations


@router.get("/parcels/{tracking_code}/notifications", response_model=DeliveryStatusResponse)
async def read_delivery_status(
    tracking_code: str,
    unit_of_work: UnitOfWork,
    authorizer: Authorizer,
    authorization: BearerHeader = None,
) -> DeliveryStatusResponse:
    """What went out for one parcel — for support answering "was I told?".

    Support and Operations only, and the response carries no body, no recipient and no
    delivery code.
    """
    actor = await _authorize(
        authorizer=authorizer,
        header=authorization,
        command=NotificationCommand.DELIVERY_READ,
    )
    if not (actor.is_support or actor.is_operations):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})
    unit_of_work.begin()
    try:
        found = unit_of_work.notifications.list_for_tracking_code(tracking_code)
        unit_of_work.commit()
    except Exception:
        unit_of_work.rollback()
        raise
    return DeliveryStatusResponse(
        tracking_code=tracking_code,
        notifications=[_summary(item) for item in found],
    )


# ------------------------------------------------------------------ responses


def _preference_response(preference: ChannelPreference) -> PreferenceResponse:
    return PreferenceResponse(
        preference_id=preference.preference_id,
        category=preference.category.value,
        channel=preference.channel.value,
        enabled=preference.enabled,
        mandatory=is_mandatory(
            preference.category, preference.channel, Audience.RECEIVER
        ),
        version=preference.version,
    )


def _entry_response(entry: CentreEntry) -> CentreEntryResponse:
    return CentreEntryResponse(
        entry_id=entry.entry_id,
        category=entry.category.value,
        title=entry.title,
        body=entry.body,
        tracking_code=entry.tracking_code,
        created_at=entry.created_at,
        read=entry.is_read,
    )


def _summary(notification: Notification) -> NotificationSummary:
    return NotificationSummary(
        notification_id=notification.notification_id,
        audience=notification.audience.value,
        channel=notification.channel.value,
        category=notification.category.value,
        template_code=notification.template_code,
        status=notification.status.value,
        attempt_count=notification.attempt_count,
        failure_reason=notification.failure_reason,
        sent_at=notification.sent_at,
    )
