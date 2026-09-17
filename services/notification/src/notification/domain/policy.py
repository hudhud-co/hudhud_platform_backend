"""Who gets told what, on which channel, for each triggering fact.

This module is the v6.3 notification chapters expressed as data. It is pure — no I/O, no
clock — so the rules can be read and tested directly against the text they come from.

**On acceptance** (v6.3 p.20, CHANGED IN V6.3), the moment a parcel is scanned and
accepted at a merchant pickup *or* a hub drop-off:

* the sender gets an app notification confirming acceptance;
* the receiver **always** gets an SMS with the delivery code and a tracking link;
* the receiver also gets an app notification *if the app is installed*;
* if the number has WhatsApp, a more detailed WhatsApp message encouraging app install.

**Before the driver sets off** (v6.3 p.26): app notification, SMS and a phone call — three
ways at once, giving an ETA rather than an exact time.

**At delivery** (v6.3 p.14): an intact-seal confirmation to the merchant, but only when
the merchant bought the packaging add-on.
"""

from __future__ import annotations

from dataclasses import dataclass

from notification.domain.value_objects import Audience, Category, Channel, Reachability

#: Template codes. Bodies live in configuration; the codes are the contract.
TPL_ACCEPTED_SENDER_APP = "acceptance.sender.app"
TPL_ACCEPTED_RECEIVER_SMS = "acceptance.receiver.sms"
TPL_ACCEPTED_RECEIVER_APP = "acceptance.receiver.app"
TPL_ACCEPTED_RECEIVER_WHATSAPP = "acceptance.receiver.whatsapp"
TPL_PRE_DELIVERY_RECEIVER_APP = "pre_delivery.receiver.app"
TPL_PRE_DELIVERY_RECEIVER_SMS = "pre_delivery.receiver.sms"
TPL_PRE_DELIVERY_RECEIVER_CALL = "pre_delivery.receiver.call"
TPL_SEAL_INTACT_MERCHANT_APP = "seal_intact.merchant.app"
TPL_OPERATIONS_DRIVER_APP = "operations.driver.app"


@dataclass(frozen=True, slots=True)
class PlannedMessage:
    audience: Audience
    channel: Channel
    template_code: str
    #: True when the receiver's copy must carry the delivery code (NTF-03).
    carries_delivery_code: bool = False
    #: True when the body carries the "install the app" encouragement (NTF-05, NTF-06).
    carries_install_encouragement: bool = False


#: v6.3 p.20. Order is meaningful only for reading; all four are sent together.
ACCEPTANCE_FAN_OUT: tuple[PlannedMessage, ...] = (
    PlannedMessage(Audience.SENDER, Channel.APP, TPL_ACCEPTED_SENDER_APP),
    PlannedMessage(
        Audience.RECEIVER,
        Channel.SMS,
        TPL_ACCEPTED_RECEIVER_SMS,
        carries_delivery_code=True,
    ),
    PlannedMessage(Audience.RECEIVER, Channel.APP, TPL_ACCEPTED_RECEIVER_APP),
    PlannedMessage(
        Audience.RECEIVER,
        Channel.WHATSAPP,
        TPL_ACCEPTED_RECEIVER_WHATSAPP,
        carries_install_encouragement=True,
    ),
)

#: v6.3 p.26 — "notified three ways at once".
PRE_DELIVERY_FAN_OUT: tuple[PlannedMessage, ...] = (
    PlannedMessage(Audience.RECEIVER, Channel.APP, TPL_PRE_DELIVERY_RECEIVER_APP),
    PlannedMessage(Audience.RECEIVER, Channel.SMS, TPL_PRE_DELIVERY_RECEIVER_SMS),
    PlannedMessage(Audience.RECEIVER, Channel.PHONE_CALL, TPL_PRE_DELIVERY_RECEIVER_CALL),
)

#: v6.3 p.14 — only when the merchant bought Hudhud packaging.
SEAL_CONFIRMATION_FAN_OUT: tuple[PlannedMessage, ...] = (
    PlannedMessage(Audience.MERCHANT, Channel.APP, TPL_SEAL_INTACT_MERCHANT_APP),
)

FAN_OUT_BY_CATEGORY: dict[Category, tuple[PlannedMessage, ...]] = {
    Category.ACCEPTANCE: ACCEPTANCE_FAN_OUT,
    Category.PRE_DELIVERY: PRE_DELIVERY_FAN_OUT,
    Category.SEAL_CONFIRMATION: SEAL_CONFIRMATION_FAN_OUT,
    Category.OPERATIONS_BULLETIN: (
        PlannedMessage(Audience.DRIVER, Channel.APP, TPL_OPERATIONS_DRIVER_APP),
    ),
}


def fan_out_for(category: Category) -> tuple[PlannedMessage, ...]:
    return FAN_OUT_BY_CATEGORY.get(category, ())


def reachable_messages(
    messages: tuple[PlannedMessage, ...], reachability: Reachability
) -> tuple[tuple[PlannedMessage, ...], tuple[PlannedMessage, ...]]:
    """Split a fan-out into what can be delivered and what cannot.

    The app copy and the WhatsApp copy are conditional by design — v6.3 says "if the app
    is installed" and "if the receiver's number has WhatsApp". The SMS never is.
    """
    deliverable = tuple(m for m in messages if reachability.supports(m.channel))
    unreachable = tuple(m for m in messages if not reachability.supports(m.channel))
    return deliverable, unreachable
