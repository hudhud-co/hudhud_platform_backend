"""Value objects for notification channels, categories and delivery outcomes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Channel(StrEnum):
    """How a message reaches someone.

    v6.3 p.20 names exactly these, and the distinction between them is not cosmetic: SMS
    always works, the app only if it is installed, and WhatsApp only if the number has an
    account. A phone call is a person dialling, recorded so the attempt is auditable.
    """

    SMS = "SMS"
    APP = "APP"
    WHATSAPP = "WHATSAPP"
    PHONE_CALL = "PHONE_CALL"


class Category(StrEnum):
    """Why a message is being sent.

    Customer App v3 `notifPrefs` groups preferences by what the message is about, not by
    channel, which is what makes a per-category opt-out meaningful.
    """

    #: v6.3 p.20 — sent the moment a parcel is accepted, at pickup or at a hub drop-off.
    ACCEPTANCE = "ACCEPTANCE"
    #: v6.3 p.26 — sent right before a driver sets off to deliver.
    PRE_DELIVERY = "PRE_DELIVERY"
    STATUS_CHANGE = "STATUS_CHANGE"
    #: v6.3 p.14 — the intact-seal confirmation a merchant receives at delivery.
    SEAL_CONFIRMATION = "SEAL_CONFIRMATION"
    #: Operations reaching a driver (DRV:`notifications`).
    OPERATIONS_BULLETIN = "OPERATIONS_BULLETIN"


class Audience(StrEnum):
    SENDER = "SENDER"
    RECEIVER = "RECEIVER"
    MERCHANT = "MERCHANT"
    DRIVER = "DRIVER"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    #: Not sent because the recipient opted out of this category on this channel.
    SUPPRESSED = "SUPPRESSED"
    #: Not sent because the channel is not reachable for this recipient.
    UNREACHABLE = "UNREACHABLE"


DELIVERY_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.PENDING: frozenset(
        {
            DeliveryStatus.SENT,
            DeliveryStatus.FAILED,
            DeliveryStatus.SUPPRESSED,
            DeliveryStatus.UNREACHABLE,
        }
    ),
    DeliveryStatus.SENT: frozenset({DeliveryStatus.DELIVERED, DeliveryStatus.FAILED}),
    DeliveryStatus.DELIVERED: frozenset(),
    DeliveryStatus.FAILED: frozenset({DeliveryStatus.SENT}),
    DeliveryStatus.SUPPRESSED: frozenset(),
    DeliveryStatus.UNREACHABLE: frozenset(),
}


#: Messages a recipient cannot switch off.
#:
#: v6.3 p.20 is unambiguous that the receiver **always** gets the acceptance SMS, and that
#: SMS carries the delivery code. Letting a preference suppress it would leave a receiver
#: unable to take their own parcel, so the preference layer is not consulted for it.
MANDATORY: frozenset[tuple[Category, Channel, Audience]] = frozenset(
    {(Category.ACCEPTANCE, Channel.SMS, Audience.RECEIVER)}
)


def is_mandatory(category: Category, channel: Channel, audience: Audience) -> bool:
    return (category, channel, audience) in MANDATORY


@dataclass(frozen=True, slots=True)
class Reachability:
    """What we know about how a recipient can be reached (v6.3 p.20).

    Both flags default to ``False``: assuming the app is installed would silently drop the
    WhatsApp fallback, and assuming WhatsApp exists would produce undeliverable messages.
    """

    app_installed: bool = False
    whatsapp_available: bool = False

    def supports(self, channel: Channel) -> bool:
        if channel is Channel.SMS or channel is Channel.PHONE_CALL:
            return True
        if channel is Channel.APP:
            return self.app_installed
        return self.whatsapp_available


#: v6.3 p.20 — WhatsApp and the tracking website both carry the same encouragement:
#: install the app to specify a handover time window and an exact address.
APP_INSTALL_ENCOURAGEMENT = (
    "Install the HUDHUD app to choose your handover time window and give your exact "
    "address and location."
)

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_TRACKING_CODE = re.compile(r"^SHP-\d{8}-\d{6}$")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0") and len(digits) >= 10:
        digits = "+964" + digits[1:]
    if not digits.startswith("+"):
        digits = "+" + digits
    if not _E164.match(digits):
        msg = f"not a usable phone number: {raw}"
        raise ValueError(msg)
    return digits


def phone_last4(phone: str) -> str:
    return phone[-4:]


def is_tracking_code(value: str) -> bool:
    return bool(_TRACKING_CODE.match(value))
