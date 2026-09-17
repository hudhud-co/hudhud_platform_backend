"""Per-channel notification preferences and the in-app notification centre.

Customer App v3 `notifPrefs` groups preferences by what a message is about — "We only send
three kinds of notification" — so an opt-out is per category *and* channel rather than a
single global switch.

One category and channel cannot be switched off at all: the receiver's acceptance SMS,
because it carries the delivery code (v6.3 p.20).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from notification.domain.entities import CentreEntry, ChannelPreference
from notification.domain.errors import (
    CentreEntryNotFound,
    MandatoryNotificationCannotBeDisabled,
)
from notification.domain.value_objects import (
    Audience,
    Category,
    Channel,
    is_mandatory,
)
from notification.ports.repository import NotificationUnitOfWork


def _now() -> datetime:
    return datetime.now(tz=UTC)


class PreferenceService:
    def __init__(self, unit_of_work: NotificationUnitOfWork) -> None:
        self._uow = unit_of_work

    def set_preference(
        self,
        *,
        principal_id: UUID,
        category: Category,
        channel: Channel,
        enabled: bool,
        audience: Audience = Audience.RECEIVER,
    ) -> ChannelPreference:
        """Switch one category on one channel on or off.

        Turning a mandatory message off is refused rather than silently ignored, so the
        app can tell the person why the switch will not move.
        """
        if not enabled and is_mandatory(category, channel, audience):
            raise MandatoryNotificationCannotBeDisabled(category.value, channel.value)

        self._uow.begin()
        try:
            preference = self._uow.preferences.find(principal_id, category, channel)
            if preference is None:
                preference = ChannelPreference(
                    preference_id=uuid4(),
                    principal_id=principal_id,
                    category=category,
                    channel=channel,
                    enabled=enabled,
                    updated_at=_now(),
                )
            else:
                preference.enabled = enabled
                preference.updated_at = _now()
                preference.version += 1
            self._uow.preferences.save(preference)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return preference

    def list_preferences(self, *, principal_id: UUID) -> tuple[ChannelPreference, ...]:
        self._uow.begin()
        try:
            found = self._uow.preferences.list_for_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return found

    # ------------------------------------------------------------- centre

    def list_centre(self, *, principal_id: UUID) -> tuple[CentreEntry, ...]:
        self._uow.begin()
        try:
            found = self._uow.centre.list_for_principal(principal_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return tuple(
            sorted(found, key=lambda entry: entry.created_at or _now(), reverse=True)
        )

    def mark_read(self, *, entry_id: UUID, principal_id: UUID) -> CentreEntry:
        self._uow.begin()
        try:
            entry = self._uow.centre.get(entry_id)
            if entry is None or entry.principal_id != principal_id:
                # Not 403: confirming the id exists would leak someone else's history.
                raise CentreEntryNotFound(str(entry_id))
            if entry.read_at is None:
                entry.read_at = _now()
                entry.version += 1
                self._uow.centre.save(entry)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return entry

    def unread_count(self, *, principal_id: UUID) -> int:
        return sum(
            1 for entry in self.list_centre(principal_id=principal_id) if not entry.is_read
        )
