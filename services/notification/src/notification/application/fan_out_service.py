"""Turning a journey fact into the messages v6.3 says must go out.

The whole fan-out for one fact is planned and written in a single transaction. A partial
fan-out is the failure mode that matters here: a receiver holding a tracking link with no
delivery code, or an SMS sent twice because a redelivery re-ran half the plan.

Nothing is sent from this service's write path. Messages are recorded as `PENDING` and a
dispatcher sends them, so a transport outage never rolls back the fact that a parcel was
accepted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from notification.domain.entities import (
    CentreEntry,
    FanOutPlan,
    Notification,
    RecipientProfile,
)
from notification.domain.errors import InvalidPhoneNumber
from notification.domain.policy import PlannedMessage, fan_out_for, reachable_messages
from notification.domain.security import hash_recipient
from notification.domain.value_objects import (
    Audience,
    Category,
    Channel,
    DeliveryStatus,
    Reachability,
    is_mandatory,
    normalize_phone,
    phone_last4,
)
from notification.ports.repository import NotificationUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class RecipientRef:
    """One person a fan-out may reach.

    A receiver usually has no account — v6.3 p.20 texts them before they have ever opened
    the app — so a phone number alone is enough to be a recipient.
    """

    phone: str
    audience: Audience
    principal_id: UUID | None = None
    app_installed: bool = False
    whatsapp_available: bool = False

    @property
    def reachability(self) -> Reachability:
        return Reachability(
            app_installed=self.app_installed,
            whatsapp_available=self.whatsapp_available,
        )


@dataclass(frozen=True, slots=True)
class FanOutResult:
    plan: FanOutPlan
    notifications: tuple[Notification, ...]
    centre_entries: tuple[CentreEntry, ...]
    #: True when this fact had already been fanned out and nothing new was written.
    replayed: bool = False


class FanOutService:
    def __init__(
        self, unit_of_work: NotificationUnitOfWork, *, recipient_key: str
    ) -> None:
        self._uow = unit_of_work
        self._recipient_key = recipient_key

    # ------------------------------------------------------------- fan-out

    def fan_out(
        self,
        *,
        category: Category,
        trigger_reference: str,
        recipients: tuple[RecipientRef, ...],
        tracking_code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> FanOutResult:
        """Plan and record every message for one fact, atomically.

        ``trigger_reference`` is what makes the whole thing idempotent: the same fact
        replayed from the broker produces the same dedupe keys and writes nothing twice.
        """
        shared_context = dict(context or {})
        self._uow.begin()
        try:
            planned: list[tuple[Audience, Channel, str]] = []
            suppressed: list[tuple[Audience, Channel, str]] = []
            unreachable: list[tuple[Audience, Channel, str]] = []
            created: list[Notification] = []
            entries: list[CentreEntry] = []
            replayed = True

            for recipient in recipients:
                profile = self._upsert_profile(recipient)
                messages = tuple(
                    message
                    for message in fan_out_for(category)
                    if message.audience is recipient.audience
                )
                deliverable, blocked = reachable_messages(
                    messages, recipient.reachability
                )
                unreachable.extend(
                    (m.audience, m.channel, m.template_code) for m in blocked
                )

                for message in deliverable:
                    if self._is_suppressed(recipient, category, message):
                        suppressed.append(
                            (message.audience, message.channel, message.template_code)
                        )
                        self._record_suppressed(
                            profile=profile,
                            recipient=recipient,
                            category=category,
                            message=message,
                            trigger_reference=trigger_reference,
                            tracking_code=tracking_code,
                        )
                        continue

                    dedupe_key = self._dedupe_key(
                        trigger_reference, profile.recipient_key, message
                    )
                    if self._uow.notifications.find_by_dedupe_key(dedupe_key) is not None:
                        planned.append(
                            (message.audience, message.channel, message.template_code)
                        )
                        continue

                    replayed = False
                    notification = Notification(
                        notification_id=uuid4(),
                        recipient_key=profile.recipient_key,
                        audience=message.audience,
                        category=category,
                        channel=message.channel,
                        template_code=message.template_code,
                        status=DeliveryStatus.PENDING,
                        dedupe_key=dedupe_key,
                        tracking_code=tracking_code,
                        principal_id=recipient.principal_id,
                        context=shared_context,
                        created_at=_now(),
                    )
                    self._uow.notifications.save(notification)
                    created.append(notification)
                    planned.append(
                        (message.audience, message.channel, message.template_code)
                    )

                    # The in-app centre mirrors the app copy only, and only for someone
                    # who actually has an account to read it in.
                    if (
                        message.channel is Channel.APP
                        and recipient.principal_id is not None
                    ):
                        entry = CentreEntry(
                            entry_id=uuid4(),
                            principal_id=recipient.principal_id,
                            category=category,
                            title=message.template_code,
                            body=str(shared_context.get("summary", "")),
                            tracking_code=tracking_code,
                            created_at=_now(),
                        )
                        self._uow.centre.save(entry)
                        entries.append(entry)

            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

        return FanOutResult(
            plan=FanOutPlan(
                planned=tuple(planned),
                suppressed=tuple(suppressed),
                unreachable=tuple(unreachable),
            ),
            notifications=tuple(created),
            centre_entries=tuple(entries),
            replayed=replayed and not created,
        )

    # ------------------------------------------------------------- internals

    def _upsert_profile(self, recipient: RecipientRef) -> RecipientProfile:
        try:
            phone = normalize_phone(recipient.phone)
        except ValueError as exc:
            raise InvalidPhoneNumber(recipient.phone) from exc
        key = hash_recipient(phone, key=self._recipient_key)

        profile = self._uow.recipients.get(key)
        if profile is None:
            profile = RecipientProfile(
                recipient_key=key,
                phone_last4=phone_last4(phone),
                principal_id=recipient.principal_id,
                reachability=recipient.reachability,
                updated_at=_now(),
            )
        else:
            profile.reachability = recipient.reachability
            if recipient.principal_id is not None:
                profile.principal_id = recipient.principal_id
            profile.updated_at = _now()
            profile.version += 1
        self._uow.recipients.save(profile)
        return profile

    def _is_suppressed(
        self, recipient: RecipientRef, category: Category, message: PlannedMessage
    ) -> bool:
        """Preferences are consulted for everything except the mandatory messages.

        v6.3 p.20 makes the receiver's acceptance SMS unconditional, and it carries the
        delivery code, so the preference layer is not even asked about it.
        """
        if is_mandatory(category, message.channel, message.audience):
            return False
        if recipient.principal_id is None:
            return False
        preference = self._uow.preferences.find(
            recipient.principal_id, category, message.channel
        )
        return preference is not None and not preference.enabled

    def _record_suppressed(
        self,
        *,
        profile: RecipientProfile,
        recipient: RecipientRef,
        category: Category,
        message: PlannedMessage,
        trigger_reference: str,
        tracking_code: str | None,
    ) -> None:
        """Record the decision not to send, so an absence is explainable later."""
        dedupe_key = self._dedupe_key(trigger_reference, profile.recipient_key, message)
        if self._uow.notifications.find_by_dedupe_key(dedupe_key) is not None:
            return
        self._uow.notifications.save(
            Notification(
                notification_id=uuid4(),
                recipient_key=profile.recipient_key,
                audience=message.audience,
                category=category,
                channel=message.channel,
                template_code=message.template_code,
                status=DeliveryStatus.SUPPRESSED,
                dedupe_key=dedupe_key,
                tracking_code=tracking_code,
                principal_id=recipient.principal_id,
                created_at=_now(),
            )
        )

    @staticmethod
    def _dedupe_key(
        trigger_reference: str, recipient_key: str, message: PlannedMessage
    ) -> str:
        raw = "|".join(
            (
                trigger_reference,
                recipient_key,
                message.audience.value,
                message.channel.value,
                message.template_code,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
