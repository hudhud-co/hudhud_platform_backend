"""PostgreSQL unit of work for the Notification service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from notification.domain.entities import (
    CentreEntry,
    ChannelPreference,
    Notification,
    RecipientProfile,
)
from notification.domain.errors import StaleNotificationRecord
from notification.domain.messaging import InboxRecord, InboxStatus
from notification.domain.value_objects import (
    Audience,
    Category,
    Channel,
    DeliveryStatus,
    Reachability,
)
from notification.infrastructure.persistence.models import (
    CentreEntryRow,
    ChannelPreferenceRow,
    IntegrationInboxRow,
    NotificationRow,
    RecipientProfileRow,
)

_STAGES = ("recipients", "preferences", "notifications", "centre", "inbox")


class SqlAlchemyNotificationUnitOfWork:
    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._recipients = _RecipientRepo(self)
        self._preferences = _PreferenceRepo(self)
        self._notifications = _NotificationRepo(self)
        self._centre = _CentreRepo(self)
        self._inbox = _InboxRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entity, previous in pending["recipients"].values():
            _versioned(
                session,
                RecipientProfileRow,
                RecipientProfileRow.recipient_key == entity.recipient_key,
                previous=previous,
                values=_recipient_values(entity),
                new_row=lambda e=entity: RecipientProfileRow(
                    recipient_key=e.recipient_key, **_recipient_values(e)
                ),
            )
        for entity, previous in pending["preferences"].values():
            _versioned(
                session,
                ChannelPreferenceRow,
                ChannelPreferenceRow.preference_id == entity.preference_id,
                previous=previous,
                values=_preference_values(entity),
                new_row=lambda e=entity: ChannelPreferenceRow(
                    preference_id=e.preference_id, **_preference_values(e)
                ),
            )
        for entity, previous in pending["notifications"].values():
            _versioned(
                session,
                NotificationRow,
                NotificationRow.notification_id == entity.notification_id,
                previous=previous,
                values=_notification_values(entity),
                new_row=lambda e=entity: NotificationRow(
                    notification_id=e.notification_id, **_notification_values(e)
                ),
            )
        for entity, previous in pending["centre"].values():
            _versioned(
                session,
                CentreEntryRow,
                CentreEntryRow.entry_id == entity.entry_id,
                previous=previous,
                values=_centre_values(entity),
                new_row=lambda e=entity: CentreEntryRow(
                    entry_id=e.entry_id, **_centre_values(e)
                ),
            )
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

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


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleNotificationRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyNotificationUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _RecipientRepo(_Repo):
    def save(self, profile: RecipientProfile) -> None:
        _stage(
            self._stage_for("recipients"),
            profile.recipient_key,
            profile,
            profile.version,
        )

    def get(self, recipient_key: str) -> RecipientProfile | None:
        staged = self._stage_for("recipients").get(recipient_key)
        if staged is not None:
            return staged[0]
        row = self._session().get(RecipientProfileRow, recipient_key)
        return _recipient_entity(row) if row is not None else None

    def find_for_principal(self, principal_id: UUID) -> RecipientProfile | None:
        row = (
            self._session()
            .execute(
                select(RecipientProfileRow).where(
                    RecipientProfileRow.principal_id == principal_id
                )
            )
            .scalars()
            .first()
        )
        return _recipient_entity(row) if row is not None else None


class _PreferenceRepo(_Repo):
    def save(self, preference: ChannelPreference) -> None:
        _stage(
            self._stage_for("preferences"),
            preference.preference_id,
            preference,
            preference.version,
        )

    def find(
        self, principal_id: UUID, category: Category, channel: Channel
    ) -> ChannelPreference | None:
        for staged, _ in self._stage_for("preferences").values():
            if (
                staged.principal_id == principal_id
                and staged.category is category
                and staged.channel is channel
            ):
                return staged
        row = (
            self._session()
            .execute(
                select(ChannelPreferenceRow).where(
                    ChannelPreferenceRow.principal_id == principal_id,
                    ChannelPreferenceRow.category == category.value,
                    ChannelPreferenceRow.channel == channel.value,
                )
            )
            .scalars()
            .first()
        )
        return _preference_entity(row) if row is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[ChannelPreference, ...]:
        rows = (
            self._session()
            .execute(
                select(ChannelPreferenceRow).where(
                    ChannelPreferenceRow.principal_id == principal_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_preference_entity(row) for row in rows)


class _NotificationRepo(_Repo):
    def save(self, notification: Notification) -> None:
        _stage(
            self._stage_for("notifications"),
            notification.notification_id,
            notification,
            notification.version,
        )

    def get(self, notification_id: UUID) -> Notification | None:
        staged = self._stage_for("notifications").get(notification_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(NotificationRow, notification_id)
        return _notification_entity(row) if row is not None else None

    def find_by_dedupe_key(self, dedupe_key: str) -> Notification | None:
        for staged, _ in self._stage_for("notifications").values():
            if staged.dedupe_key == dedupe_key:
                return staged
        row = (
            self._session()
            .execute(
                select(NotificationRow).where(NotificationRow.dedupe_key == dedupe_key)
            )
            .scalars()
            .first()
        )
        return _notification_entity(row) if row is not None else None

    def list_pending(self) -> tuple[Notification, ...]:
        rows = (
            self._session()
            .execute(
                select(NotificationRow).where(
                    NotificationRow.status == DeliveryStatus.PENDING.value
                )
            )
            .scalars()
            .all()
        )
        return tuple(_notification_entity(row) for row in rows)

    def list_for_tracking_code(self, tracking_code: str) -> tuple[Notification, ...]:
        rows = (
            self._session()
            .execute(
                select(NotificationRow).where(
                    NotificationRow.tracking_code == tracking_code
                )
            )
            .scalars()
            .all()
        )
        return tuple(_notification_entity(row) for row in rows)


class _CentreRepo(_Repo):
    def save(self, entry: CentreEntry) -> None:
        _stage(self._stage_for("centre"), entry.entry_id, entry, entry.version)

    def get(self, entry_id: UUID) -> CentreEntry | None:
        staged = self._stage_for("centre").get(entry_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(CentreEntryRow, entry_id)
        return _centre_entity(row) if row is not None else None

    def list_for_principal(self, principal_id: UUID) -> tuple[CentreEntry, ...]:
        rows = (
            self._session()
            .execute(
                select(CentreEntryRow)
                .where(CentreEntryRow.principal_id == principal_id)
                .order_by(CentreEntryRow.created_at.desc())
            )
            .scalars()
            .all()
        )
        return tuple(_centre_entity(row) for row in rows)


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (record, True)

    def save(self, record: InboxRecord) -> None:
        stage = self._stage_for("inbox")
        key = (record.consumer_name, record.event_id)
        is_new = stage[key][1] if key in stage else False
        stage[key] = (record, is_new)


# ------------------------------------------------------------------ mappers


def _recipient_values(e: RecipientProfile) -> dict[str, object]:
    return {
        "phone_last4": e.phone_last4,
        "principal_id": e.principal_id,
        "app_installed": e.reachability.app_installed,
        "whatsapp_available": e.reachability.whatsapp_available,
        "locale": e.locale,
        "updated_at": e.updated_at,
        "version": e.version,
    }


def _recipient_entity(row: RecipientProfileRow) -> RecipientProfile:
    return RecipientProfile(
        recipient_key=row.recipient_key,
        phone_last4=row.phone_last4,
        principal_id=row.principal_id,  # type: ignore[arg-type]
        reachability=Reachability(
            app_installed=bool(row.app_installed),
            whatsapp_available=bool(row.whatsapp_available),
        ),
        locale=row.locale,
        updated_at=row.updated_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _preference_values(e: ChannelPreference) -> dict[str, object]:
    return {
        "principal_id": e.principal_id,
        "category": e.category.value,
        "channel": e.channel.value,
        "enabled": e.enabled,
        "updated_at": e.updated_at,
        "version": e.version,
    }


def _preference_entity(row: ChannelPreferenceRow) -> ChannelPreference:
    return ChannelPreference(
        preference_id=row.preference_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        category=Category(row.category),
        channel=Channel(row.channel),
        enabled=bool(row.enabled),
        updated_at=row.updated_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _notification_values(e: Notification) -> dict[str, object]:
    return {
        "recipient_key": e.recipient_key,
        "audience": e.audience.value,
        "category": e.category.value,
        "channel": e.channel.value,
        "template_code": e.template_code,
        "status": e.status.value,
        "dedupe_key": e.dedupe_key,
        "tracking_code": e.tracking_code,
        "principal_id": e.principal_id,
        "context": dict(e.context),
        "created_at": e.created_at,
        "sent_at": e.sent_at,
        "delivered_at": e.delivered_at,
        "failure_reason": e.failure_reason,
        "attempt_count": e.attempt_count,
        "version": e.version,
    }


def _notification_entity(row: NotificationRow) -> Notification:
    return Notification(
        notification_id=row.notification_id,  # type: ignore[arg-type]
        recipient_key=row.recipient_key,
        audience=Audience(row.audience),
        category=Category(row.category),
        channel=Channel(row.channel),
        template_code=row.template_code,
        status=DeliveryStatus(row.status),
        dedupe_key=row.dedupe_key,
        tracking_code=row.tracking_code,
        principal_id=row.principal_id,  # type: ignore[arg-type]
        context=dict(row.context or {}),
        created_at=row.created_at,  # type: ignore[arg-type]
        sent_at=row.sent_at,  # type: ignore[arg-type]
        delivered_at=row.delivered_at,  # type: ignore[arg-type]
        failure_reason=row.failure_reason,
        attempt_count=row.attempt_count,
        version=row.version,
    )


def _centre_values(e: CentreEntry) -> dict[str, object]:
    return {
        "principal_id": e.principal_id,
        "category": e.category.value,
        "title": e.title,
        "body": e.body,
        "tracking_code": e.tracking_code,
        "created_at": e.created_at,
        "read_at": e.read_at,
        "version": e.version,
    }


def _centre_entity(row: CentreEntryRow) -> CentreEntry:
    return CentreEntry(
        entry_id=row.entry_id,  # type: ignore[arg-type]
        principal_id=row.principal_id,  # type: ignore[arg-type]
        category=Category(row.category),
        title=row.title,
        body=row.body,
        tracking_code=row.tracking_code,
        created_at=row.created_at,  # type: ignore[arg-type]
        read_at=row.read_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _inbox_values(e: InboxRecord) -> dict[str, object]:
    return {
        "inbox_id": e.inbox_id,
        "consumer_name": e.consumer_name,
        "event_id": e.event_id,
        **_inbox_update_values(e),
    }


def _inbox_update_values(e: InboxRecord) -> dict[str, object]:
    return {
        "event_type": e.event_type,
        "event_version": e.event_version,
        "status": e.status.value,
        "received_at": e.received_at,
        "attempt_count": e.attempt_count,
        "processing_started_at": e.processing_started_at,
        "processing_lease_until": e.processing_lease_until,
        "processed_at": e.processed_at,
        "last_error_code": e.last_error_code,
        "payload_json": e.payload_json,
    }


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,  # type: ignore[arg-type]
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        payload_json=dict(row.payload_json or {}),
    )
