"""Shared builders for Notification tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from notification.application.dispatch_service import DispatchService
from notification.application.fan_out_service import FanOutService, RecipientRef
from notification.application.inbox_service import InboxService
from notification.application.preference_service import PreferenceService
from notification.domain.policy import (
    TPL_ACCEPTED_RECEIVER_APP,
    TPL_ACCEPTED_RECEIVER_SMS,
    TPL_ACCEPTED_RECEIVER_WHATSAPP,
    TPL_ACCEPTED_SENDER_APP,
    TPL_PRE_DELIVERY_RECEIVER_APP,
    TPL_PRE_DELIVERY_RECEIVER_CALL,
    TPL_PRE_DELIVERY_RECEIVER_SMS,
    TPL_SEAL_INTACT_MERCHANT_APP,
)
from notification.domain.value_objects import Audience, Channel
from notification.infrastructure.memory import InMemoryNotificationUnitOfWork
from notification.infrastructure.transports import RecordingTransport

RECIPIENT_KEY = "notification-recipient-key-dev-only"

SENDER = UUID("11111111-1111-4111-8111-111111111111")
RECEIVER = UUID("22222222-2222-4222-8222-222222222222")
MERCHANT = UUID("33333333-3333-4333-8333-333333333333")
DRIVER = UUID("44444444-4444-4444-8444-444444444444")

SENDER_PHONE = "+9647701111111"
RECEIVER_PHONE = "+9647702222222"
MERCHANT_PHONE = "+9647703333333"

TRACKING = "SHP-20260914-000101"

#: Bodies used in tests. The real ones are configuration; what matters here is which
#: placeholders each template consumes.
TEMPLATES = {
    TPL_ACCEPTED_SENDER_APP: "Your parcel {tracking_code} has been accepted.",
    TPL_ACCEPTED_RECEIVER_SMS: (
        "HUDHUD: parcel {tracking_code} is on its way. "
        "Your delivery code is {delivery_code}. Track it: {tracking_link}"
    ),
    TPL_ACCEPTED_RECEIVER_APP: "A parcel {tracking_code} is on its way to you.",
    TPL_ACCEPTED_RECEIVER_WHATSAPP: (
        "A parcel {tracking_code} is on its way to you. {install_encouragement}"
    ),
    TPL_PRE_DELIVERY_RECEIVER_APP: "Your courier is setting off. ETA {eta}.",
    TPL_PRE_DELIVERY_RECEIVER_SMS: "HUDHUD: your courier is setting off. ETA {eta}.",
    TPL_PRE_DELIVERY_RECEIVER_CALL: "Courier setting off, ETA {eta}.",
    TPL_SEAL_INTACT_MERCHANT_APP: "Parcel {tracking_code} was delivered with its seal intact.",
}

DELIVERY_CODE = "482913"


def build_store() -> InMemoryNotificationUnitOfWork:
    return InMemoryNotificationUnitOfWork()


def fan_out_service(uow: InMemoryNotificationUnitOfWork) -> FanOutService:
    return FanOutService(uow, recipient_key=RECIPIENT_KEY)


def preference_service(uow: InMemoryNotificationUnitOfWork) -> PreferenceService:
    return PreferenceService(uow)


def inbox_service(uow: InMemoryNotificationUnitOfWork, **kwargs) -> InboxService:
    return InboxService(uow, **kwargs)


def recording_transports(
    *, accept: bool = True
) -> dict[Channel, RecordingTransport]:
    return {channel: RecordingTransport(channel, accept=accept) for channel in Channel}


def dispatch_service(
    uow: InMemoryNotificationUnitOfWork,
    transports: dict[Channel, RecordingTransport] | None = None,
    **kwargs,
) -> DispatchService:
    return DispatchService(
        uow,
        transports=transports if transports is not None else recording_transports(),
        templates=kwargs.pop("templates", TEMPLATES),
        **kwargs,
    )


def sender_ref(**kwargs) -> RecipientRef:
    return RecipientRef(
        phone=kwargs.pop("phone", SENDER_PHONE),
        audience=Audience.SENDER,
        principal_id=kwargs.pop("principal_id", SENDER),
        app_installed=kwargs.pop("app_installed", True),
        **kwargs,
    )


def receiver_ref(**kwargs) -> RecipientRef:
    """A receiver with no account and no app — v6.3's default case."""
    return RecipientRef(
        phone=kwargs.pop("phone", RECEIVER_PHONE),
        audience=Audience.RECEIVER,
        principal_id=kwargs.pop("principal_id", None),
        app_installed=kwargs.pop("app_installed", False),
        whatsapp_available=kwargs.pop("whatsapp_available", False),
        **kwargs,
    )


def merchant_ref(**kwargs) -> RecipientRef:
    return RecipientRef(
        phone=kwargs.pop("phone", MERCHANT_PHONE),
        audience=Audience.MERCHANT,
        principal_id=kwargs.pop("principal_id", MERCHANT),
        app_installed=kwargs.pop("app_installed", True),
        **kwargs,
    )


def acceptance_context() -> dict[str, str]:
    return {
        "tracking_link": f"https://track.hudhud.iq/{TRACKING}",
        "summary": "Your parcel has been accepted.",
    }


def new_id() -> UUID:
    return uuid4()
