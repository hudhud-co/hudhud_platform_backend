"""Persistence ports for the Notification service.

There is deliberately **no outbox port**. Notification is a consumer: it turns journey
facts other services publish into messages people receive, and publishes nothing of its
own. Carrying an outbox table nobody writes to would be dead infrastructure that later
readers would assume was in use.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from notification.domain.entities import (
    CentreEntry,
    ChannelPreference,
    Notification,
    RecipientProfile,
)
from notification.domain.messaging import InboxRecord
from notification.domain.value_objects import Category, Channel


class RecipientRepository(Protocol):
    def save(self, profile: RecipientProfile) -> None: ...

    def get(self, recipient_key: str) -> RecipientProfile | None: ...

    def find_for_principal(self, principal_id: UUID) -> RecipientProfile | None: ...


class PreferenceRepository(Protocol):
    def save(self, preference: ChannelPreference) -> None: ...

    def find(
        self, principal_id: UUID, category: Category, channel: Channel
    ) -> ChannelPreference | None: ...

    def list_for_principal(self, principal_id: UUID) -> tuple[ChannelPreference, ...]: ...


class NotificationRepository(Protocol):
    def save(self, notification: Notification) -> None: ...

    def get(self, notification_id: UUID) -> Notification | None: ...

    def find_by_dedupe_key(self, dedupe_key: str) -> Notification | None: ...

    def list_pending(self) -> tuple[Notification, ...]: ...

    def list_for_tracking_code(self, tracking_code: str) -> tuple[Notification, ...]: ...


class CentreRepository(Protocol):
    def save(self, entry: CentreEntry) -> None: ...

    def get(self, entry_id: UUID) -> CentreEntry | None: ...

    def list_for_principal(self, principal_id: UUID) -> tuple[CentreEntry, ...]: ...


class InboxRepository(Protocol):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None: ...

    def insert(self, record: InboxRecord) -> None: ...

    def save(self, record: InboxRecord) -> None: ...


class NotificationUnitOfWork(Protocol):
    @property
    def recipients(self) -> RecipientRepository: ...

    @property
    def preferences(self) -> PreferenceRepository: ...

    @property
    def notifications(self) -> NotificationRepository: ...

    @property
    def centre(self) -> CentreRepository: ...

    @property
    def inbox(self) -> InboxRepository: ...

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
