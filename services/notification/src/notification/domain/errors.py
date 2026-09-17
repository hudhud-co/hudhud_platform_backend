"""Domain errors for the Notification service."""

from __future__ import annotations


class NotificationError(Exception):
    """Base Notification domain error."""


class NotificationNotFound(NotificationError):
    def __init__(self, notification_id: str) -> None:
        self.notification_id = notification_id
        super().__init__(f"notification not found: {notification_id}")


class CentreEntryNotFound(NotificationError):
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"notification centre entry not found: {entry_id}")


class DeliveryTransitionNotAllowed(NotificationError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} notification to {target}")


class MandatoryNotificationCannotBeDisabled(NotificationError):
    """NTF-03 — the receiver **always** gets the acceptance SMS (v6.3 p.20).

    That SMS carries the delivery code. Allowing it to be switched off would leave a
    receiver unable to take their own parcel.
    """

    def __init__(self, category: str, channel: str) -> None:
        self.category = category
        self.channel = channel
        super().__init__(
            f"{category} on {channel} always goes to the receiver and cannot be "
            "switched off — it carries the delivery code (v6.3 p.20)"
        )


class DeliveryCodeMustNotBeStored(NotificationError):
    """Raised when something tries to put a delivery code into a stored field.

    A guard rather than a comment: the code belongs in the message that leaves the
    service and nowhere else.
    """

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(
            f"a delivery code must never be stored — attempted in {field_name}"
        )


class InvalidPhoneNumber(NotificationError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable phone number: {raw}")


class TransportUnavailable(NotificationError):
    """A channel's transport could not be reached — never a recipient's fault."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        super().__init__(f"{channel} transport is unavailable")


class TemplateNotFound(NotificationError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"notification template not found: {code}")


class StaleNotificationRecord(NotificationError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
