"""In-memory Notification unit of work for unit tests."""

from __future__ import annotations

import copy
from uuid import UUID

from notification.domain.entities import (
    CentreEntry,
    ChannelPreference,
    Notification,
    RecipientProfile,
)
from notification.domain.messaging import InboxRecord
from notification.domain.value_objects import Category, Channel, DeliveryStatus

_COLLECTIONS = (
    "recipients",
    "preferences",
    "notifications",
    "centre",
    "inbox",
)


class InMemoryNotificationDatabase:
    """The rows. Shared by every request, exactly as a database is.

    Split from the unit of work deliberately. A unit of work is *one request's*
    transaction; the rows outlive it. Holding both in one object is what allowed a single
    instance to be stored on `app.state` and serve every request at once — measured
    against real PostgreSQL, 1 of 40 concurrent requests succeeded and the rest raised
    "transaction already active".
    """

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {name: {} for name in _COLLECTIONS}


class InMemoryNotificationUnitOfWork:
    def __init__(self, database: InMemoryNotificationDatabase | None = None) -> None:
        self._database = database or InMemoryNotificationDatabase()
        self._tx: dict[str, dict] | None = None
        self._recipients = _RecipientRepo(self)
        self._preferences = _PreferenceRepo(self)
        self._notifications = _NotificationRepo(self)
        self._centre = _CentreRepo(self)
        self._inbox = _InboxRepo(self)

    @property
    def database(self) -> InMemoryNotificationDatabase:
        """The shared rows, so a factory can give the next request the same data."""
        return self._database

    def new_unit_of_work(self) -> InMemoryNotificationUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryNotificationUnitOfWork(self._database)


    def as_committed(self) -> InMemoryNotificationUnitOfWork:
        """A view whose "transaction" is the committed rows, for inspection and seeding.

        Service code never uses this: a service opens a real transaction, and
        :meth:`_working` refuses it otherwise. Tests use it to look at what was committed
        without opening one, and to seed rows before exercising a service — both of which
        would otherwise have to wrap every assertion in begin/rollback and would read far
        worse for no extra safety.

        Writes through this view land straight in the committed rows, which is what
        seeding wants and why it is named for it.
        """
        view = InMemoryNotificationUnitOfWork(self._database)
        view._tx = self._database.collections
        return view

    def begin(self) -> None:
        if self._tx is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx = copy.deepcopy(self._database.collections)

    def commit(self) -> None:
        if self._tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._database.collections = self._tx
        self._tx = None

    def rollback(self) -> None:
        self._tx = None

    def _working(self, name: str) -> dict:
        """Refuse a read or a write outside a transaction, as the store does.

        This used to fall back to the committed rows, making the double *more*
        permissive than SQLAlchemy — so a service method that forgot to open a
        transaction passed every unit test and failed only against PostgreSQL.
        """
        if self._tx is None:
            msg = (
                f"no active transaction: {name} was accessed outside begin()/commit(). "
                "The SQLAlchemy store raises here too."
            )
            raise RuntimeError(msg)
        return self._tx[name]

    @property
    def recipients(self) -> _RecipientRepo:
        return self._recipients

    @property
    def preferences(self) -> _PreferenceRepo:
        return self._preferences

    @property
    def notifications(self) -> _NotificationRepo:
        return self._notifications

    @property
    def centre(self) -> _CentreRepo:
        return self._centre

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


class _Repo:
    def __init__(self, store: InMemoryNotificationUnitOfWork) -> None:
        self._store = store


class _RecipientRepo(_Repo):
    def save(self, profile: RecipientProfile) -> None:
        self._store._working("recipients")[profile.recipient_key] = copy.deepcopy(profile)

    def get(self, recipient_key: str) -> RecipientProfile | None:
        found = self._store._working("recipients").get(recipient_key)
        return copy.deepcopy(found) if found is not None else None

    def find_for_principal(self, principal_id: UUID) -> RecipientProfile | None:
        for profile in self._store._working("recipients").values():
            if profile.principal_id == principal_id:
                return copy.deepcopy(profile)
        return None


class _PreferenceRepo(_Repo):
    def save(self, preference: ChannelPreference) -> None:
        self._store._working("preferences")[preference.preference_id] = copy.deepcopy(
            preference
        )

    def find(
        self, principal_id: UUID, category: Category, channel: Channel
    ) -> ChannelPreference | None:
        for preference in self._store._working("preferences").values():
            if (
                preference.principal_id == principal_id
                and preference.category is category
                and preference.channel is channel
            ):
                return copy.deepcopy(preference)
        return None

    def list_for_principal(self, principal_id: UUID) -> tuple[ChannelPreference, ...]:
        return tuple(
            copy.deepcopy(preference)
            for preference in self._store._working("preferences").values()
            if preference.principal_id == principal_id
        )


class _NotificationRepo(_Repo):
    def save(self, notification: Notification) -> None:
        self._store._working("notifications")[
            notification.notification_id
        ] = copy.deepcopy(notification)

    def get(self, notification_id: UUID) -> Notification | None:
        found = self._store._working("notifications").get(notification_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_dedupe_key(self, dedupe_key: str) -> Notification | None:
        for notification in self._store._working("notifications").values():
            if notification.dedupe_key == dedupe_key:
                return copy.deepcopy(notification)
        return None

    def list_pending(self) -> tuple[Notification, ...]:
        return tuple(
            copy.deepcopy(notification)
            for notification in self._store._working("notifications").values()
            if notification.status is DeliveryStatus.PENDING
        )

    def list_for_tracking_code(self, tracking_code: str) -> tuple[Notification, ...]:
        return tuple(
            copy.deepcopy(notification)
            for notification in self._store._working("notifications").values()
            if notification.tracking_code == tracking_code
        )


class _CentreRepo(_Repo):
    def save(self, entry: CentreEntry) -> None:
        self._store._working("centre")[entry.entry_id] = copy.deepcopy(entry)

    def get(self, entry_id: UUID) -> CentreEntry | None:
        found = self._store._working("centre").get(entry_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[CentreEntry, ...]:
        return tuple(
            copy.deepcopy(entry)
            for entry in self._store._working("centre").values()
            if entry.principal_id == principal_id
        )


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        found = self._store._working("inbox").get((consumer_name, event_id))
        return copy.deepcopy(found) if found is not None else None

    def insert(self, record: InboxRecord) -> None:
        rows = self._store._working("inbox")
        key = (record.consumer_name, record.event_id)
        if key in rows:
            msg = f"duplicate inbox delivery: {key}"
            raise ValueError(msg)
        rows[key] = copy.deepcopy(record)

    def save(self, record: InboxRecord) -> None:
        self._store._working("inbox")[
            (record.consumer_name, record.event_id)
        ] = copy.deepcopy(record)
