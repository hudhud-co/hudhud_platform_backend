"""Sending what the fan-out planned.

This is the only place a delivery code exists inside this service, and it exists for the
length of one function call. It is supplied by the caller, handed to the transport, and
never written to a notification row, a centre entry, an event, or a log line. The
`Notification` record deliberately has no field it could go in.

Sending is separated from planning so that a transport outage cannot roll back the fact
that a parcel was accepted: the fan-out commits, and dispatch retries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from notification.domain.entities import Notification
from notification.domain.errors import (
    DeliveryCodeMustNotBeStored,
    DeliveryTransitionNotAllowed,
    NotificationNotFound,
    TemplateNotFound,
    TransportUnavailable,
)
from notification.domain.policy import (
    TPL_ACCEPTED_RECEIVER_SMS,
    TPL_ACCEPTED_RECEIVER_WHATSAPP,
)
from notification.domain.value_objects import (
    APP_INSTALL_ENCOURAGEMENT,
    Channel,
    DeliveryStatus,
)
from notification.ports.repository import NotificationUnitOfWork
from notification.ports.transport import (
    ChannelTransport,
    OutboundMessage,
    TransportUnavailableError,
)

#: Templates whose rendered body must carry the delivery code (NTF-03).
CODE_BEARING_TEMPLATES: frozenset[str] = frozenset({TPL_ACCEPTED_RECEIVER_SMS})

#: Templates whose body carries the install encouragement (NTF-05, NTF-06).
ENCOURAGEMENT_TEMPLATES: frozenset[str] = frozenset(
    {TPL_ACCEPTED_RECEIVER_WHATSAPP}
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    notification: Notification
    sent: bool
    reason: str | None = None


class DispatchService:
    def __init__(
        self,
        unit_of_work: NotificationUnitOfWork,
        *,
        transports: dict[Channel, ChannelTransport],
        templates: dict[str, str],
        recipient_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._transports = transports
        self._templates = templates
        # A notification stores a *hashed* recipient. Turning that back into an address is
        # the composition root's job, which is what keeps the address out of this service.
        self._resolve = recipient_resolver

    def dispatch(
        self,
        *,
        notification_id: UUID,
        recipient_address: str,
        delivery_code: str | None = None,
    ) -> DispatchOutcome:
        """Send one planned message.

        ``delivery_code`` is used to render the body and is then dropped. It is never
        assigned to the notification, because the notification has nowhere to put it.
        """
        self._uow.begin()
        try:
            notification = self._load(notification_id)
            if notification.status is not DeliveryStatus.PENDING:
                if notification.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}:
                    # Already sent: a redelivery must not send a second message.
                    self._uow.commit()
                    return DispatchOutcome(
                        notification=notification, sent=False, reason="already_sent"
                    )
                if not notification.can_transition_to(DeliveryStatus.SENT):
                    raise DeliveryTransitionNotAllowed(
                        notification.status.value, DeliveryStatus.SENT.value
                    )

            body = self._render(notification, delivery_code)
            transport = self._transports.get(notification.channel)
            if transport is None:
                raise TransportUnavailable(notification.channel.value)

            message = OutboundMessage(
                channel=notification.channel,
                recipient=recipient_address,
                body=body,
                delivery_code=delivery_code
                if notification.template_code in CODE_BEARING_TEMPLATES
                else None,
            )
            notification.attempt_count += 1
            try:
                result = transport.send(message)
            except TransportUnavailableError as exc:
                notification.status = DeliveryStatus.FAILED
                notification.failure_reason = "transport_unavailable"
                notification.version += 1
                self._uow.notifications.save(notification)
                self._uow.commit()
                raise TransportUnavailable(notification.channel.value) from exc

            if result.accepted:
                notification.status = DeliveryStatus.SENT
                notification.sent_at = _now()
                notification.failure_reason = None
            else:
                notification.status = DeliveryStatus.FAILED
                notification.failure_reason = result.failure_reason or "rejected"
            notification.version += 1
            self._assert_no_code_stored(notification, delivery_code)
            self._uow.notifications.save(notification)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return DispatchOutcome(
            notification=notification,
            sent=notification.status is DeliveryStatus.SENT,
            reason=notification.failure_reason,
        )

    def mark_delivered(self, *, notification_id: UUID) -> Notification:
        """A provider receipt. Separate from ``SENT`` because they mean different things."""
        self._uow.begin()
        try:
            notification = self._load(notification_id)
            if not notification.can_transition_to(DeliveryStatus.DELIVERED):
                raise DeliveryTransitionNotAllowed(
                    notification.status.value, DeliveryStatus.DELIVERED.value
                )
            notification.status = DeliveryStatus.DELIVERED
            notification.delivered_at = _now()
            notification.version += 1
            self._uow.notifications.save(notification)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return notification

    def pending(self) -> tuple[Notification, ...]:
        self._uow.begin()
        try:
            found = self._uow.notifications.list_pending()
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- rendering

    def _render(self, notification: Notification, delivery_code: str | None) -> str:
        template = self._templates.get(notification.template_code)
        if template is None:
            raise TemplateNotFound(notification.template_code)
        values = {
            **notification.context,
            "tracking_code": notification.tracking_code or "",
        }
        if notification.template_code in CODE_BEARING_TEMPLATES:
            values["delivery_code"] = delivery_code or ""
        if notification.template_code in ENCOURAGEMENT_TEMPLATES:
            values["install_encouragement"] = APP_INSTALL_ENCOURAGEMENT
        try:
            return template.format(**values)
        except KeyError as exc:
            raise TemplateNotFound(notification.template_code) from exc

    @staticmethod
    def _assert_no_code_stored(
        notification: Notification, delivery_code: str | None
    ) -> None:
        """Belt and braces: fail loudly if a code ever reaches a stored field.

        The `Notification` dataclass has no code field, so this can only trip if someone
        smuggles one through `context` — which is exactly the mistake worth catching.
        """
        if not delivery_code:
            return
        for name, value in notification.context.items():
            if isinstance(value, str) and delivery_code in value:
                raise DeliveryCodeMustNotBeStored(f"context.{name}")
        if notification.failure_reason and delivery_code in notification.failure_reason:
            raise DeliveryCodeMustNotBeStored("failure_reason")

    def _load(self, notification_id: UUID) -> Notification:
        notification = self._uow.notifications.get(notification_id)
        if notification is None:
            raise NotificationNotFound(str(notification_id))
        return notification
