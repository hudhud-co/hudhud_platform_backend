"""Notification aggregates: recipient profile, preference, notification, centre entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from notification.domain.value_objects import (
    DELIVERY_TRANSITIONS,
    Audience,
    Category,
    Channel,
    DeliveryStatus,
    Reachability,
)


@dataclass(slots=True)
class RecipientProfile:
    """What is known about one person's reachability.

    Keyed by the *hash* of their phone number, because a receiver usually has no account:
    v6.3 p.20 texts them before they have ever opened the app.
    """

    recipient_key: str
    phone_last4: str
    principal_id: UUID | None = None
    reachability: Reachability = field(default_factory=Reachability)
    locale: str = "en"
    updated_at: datetime | None = None
    version: int = 1


@dataclass(slots=True)
class ChannelPreference:
    """One person's opt-out for one category on one channel (NTF-10, CUS-14).

    Stored as explicit opt-outs rather than an allowlist so that adding a new category
    does not silently switch everything off for existing users.
    """

    preference_id: UUID
    principal_id: UUID
    category: Category
    channel: Channel
    enabled: bool = True
    updated_at: datetime | None = None
    version: int = 1


@dataclass(slots=True)
class Notification:
    """One message to one recipient on one channel.

    The message body is rendered at dispatch and **not stored**: the acceptance SMS
    carries the delivery code, and a stored copy would be a second place that code lives.
    What is stored is enough to deduplicate, retry and audit — who, which channel, which
    template, which parcel, and what happened.
    """

    notification_id: UUID
    recipient_key: str
    audience: Audience
    category: Category
    channel: Channel
    template_code: str
    status: DeliveryStatus
    #: Stable across retries and redeliveries of the triggering event, so the same
    #: message is never sent twice.
    dedupe_key: str
    tracking_code: str | None = None
    principal_id: UUID | None = None
    #: Non-secret values used to render the body, e.g. a tracking link. Never the code.
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    failure_reason: str | None = None
    attempt_count: int = 0
    version: int = 1

    def can_transition_to(self, target: DeliveryStatus) -> bool:
        return target in DELIVERY_TRANSITIONS[self.status]

    @property
    def is_terminal(self) -> bool:
        return not DELIVERY_TRANSITIONS[self.status]

    @property
    def was_attempted(self) -> bool:
        return self.status in {
            DeliveryStatus.SENT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.FAILED,
        }


@dataclass(slots=True)
class CentreEntry:
    """One line in the in-app notification centre (CUS-14, NTF-09).

    Deliberately separate from `Notification`: the centre is a readable history a person
    scrolls, and it must never hold a delivery code even though the SMS that accompanied
    it did.
    """

    entry_id: UUID
    principal_id: UUID
    category: Category
    title: str
    body: str
    tracking_code: str | None = None
    created_at: datetime | None = None
    read_at: datetime | None = None
    version: int = 1

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


@dataclass(frozen=True, slots=True)
class FanOutPlan:
    """What v6.3 says should be sent for one triggering fact.

    Computed before anything is written so the whole fan-out commits together: a partial
    fan-out would leave a receiver with a tracking link and no code, or the reverse.
    """

    planned: tuple[tuple[Audience, Channel, str], ...]
    suppressed: tuple[tuple[Audience, Channel, str], ...] = ()
    unreachable: tuple[tuple[Audience, Channel, str], ...] = ()

    @property
    def channels(self) -> frozenset[Channel]:
        return frozenset(channel for _, channel, _ in self.planned)
