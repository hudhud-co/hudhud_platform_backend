"""Domain errors for the Ordering service."""

from __future__ import annotations


class OrderingError(Exception):
    """Base Ordering domain error."""


# ------------------------------------------------------------------ lookup


class OrderNotFound(OrderingError):
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"order not found: {order_id}")


class ShipmentRequestNotFound(OrderingError):
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"shipment request not found: {request_id}")


class OrderNotOpen(OrderingError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"order is {status} and cannot take new shipments")


# ------------------------------------------------------------------ content rules


class DescriptionRequired(OrderingError):
    """v6.3 p.12 Confirmed decision.

    "What is required instead is that the sender defines the parcel — a basic description
    of what it is — so it isn't registered as an unknown item." Weight and size are
    optional precisely so that this is not.
    """

    def __init__(self) -> None:
        super().__init__(
            "a parcel description is required, so the parcel is never registered as an "
            "unknown item (v6.3 p.12)"
        )


class ReceiverContactIncomplete(OrderingError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            "the receiver's " + " and ".join(missing) + " is the minimum needed to reach "
            "and route to them (v6.3 p.12)"
        )


class UnknownGovernorate(OrderingError):
    def __init__(self, governorate: str) -> None:
        self.governorate = governorate
        super().__init__(f"unknown governorate: {governorate}")


class InvalidPhoneNumber(OrderingError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable phone number: {raw}")


class UnknownGoodsCategory(OrderingError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"unknown goods category: {code}")


class ProhibitedGoods(OrderingError):
    """v6.3 terms and Customer App ``prohibited``.

    "Prohibited contents are held, the shipment is cancelled without refund, and the case
    is reported to the authorities." Refusing at creation is cheaper for everyone.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"this kind of goods is not accepted: {code}")


class ProhibitedGoodsNotAcknowledged(OrderingError):
    def __init__(self) -> None:
        super().__init__(
            "the sender must acknowledge the prohibited-goods rules before sending"
        )


# ------------------------------------------------------------------ COD


class CashOnDeliveryNotAvailableForCustomers(OrderingError):
    """v6.3 p.8, Confirmed decision (CUS-02).

    A regular customer's parcel has no COD option. Hudhud only moves the parcel for a
    personal sender — nothing is collected from the receiver.
    """

    def __init__(self) -> None:
        super().__init__(
            "a regular customer's parcel has no cash-on-delivery option — nothing is "
            "collected from the receiver (v6.3 p.8)"
        )


class CodAmountRequired(OrderingError):
    def __init__(self) -> None:
        super().__init__("a cash-on-delivery shipment must state the amount to collect")


class CodAmountNotAllowed(OrderingError):
    def __init__(self, terms: str) -> None:
        self.terms = terms
        super().__init__(f"a {terms} shipment cannot carry a cash-on-delivery amount")


class CodAmountTooLateToChange(OrderingError):
    """SHP-12.

    "If the parcel is already at the door, the old amount stands and the difference is
    settled through a claim."
    """

    def __init__(self) -> None:
        super().__init__(
            "the courier has reached the receiver — the old amount stands and the "
            "difference is settled through a claim"
        )


# ------------------------------------------------------------------ pickup


class PickupNotAvailableForCustomers(OrderingError):
    """v6.3 p.8 and p.15, Confirmed decision (CUS-03, DRV-P01).

    "A driver is assigned to collect the parcel from a merchant only — regular customers
    do not get a pickup." A customer hands the parcel in at a hub instead.
    """

    def __init__(self) -> None:
        super().__init__(
            "regular customers do not get a pickup — the parcel is handed in at a hub "
            "(v6.3 p.8)"
        )


class PickupBlockedByMissingLabels(OrderingError):
    """MER-19 — "pickup time opens once every parcel carries a label"."""

    def __init__(self, unlabelled: int) -> None:
        self.unlabelled = unlabelled
        super().__init__(
            f"{unlabelled} shipment(s) in this order have no label yet — pickup time "
            "opens once every parcel carries a label"
        )


# ------------------------------------------------------------------ labels


class LabelAlreadyLinked(OrderingError):
    def __init__(self, label_code: str) -> None:
        self.label_code = label_code
        super().__init__(f"label already linked to another shipment: {label_code}")


class LabelNotAllowedForCustomer(OrderingError):
    """v6.3 p.8, p.18 — hub staff, not the customer, stick and scan the label."""

    def __init__(self) -> None:
        super().__init__(
            "a customer does not label their own parcel — hub staff stick the label and "
            "scan it at drop-off (v6.3 p.18)"
        )


class InvalidLabelCode(OrderingError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"not a usable label code: {raw}")


# ------------------------------------------------------------------ lifecycle


class RequestTransitionNotAllowed(OrderingError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a {current} shipment request to {target}")


class RequestAlreadyInCustody(OrderingError):
    """SHP-10 — a parcel Hudhud is already carrying cannot simply be cancelled."""

    def __init__(self) -> None:
        super().__init__(
            "this parcel is already in Hudhud custody and can no longer be cancelled here"
        )


class FieldNotEditableAtThisStage(OrderingError):
    def __init__(self, field_name: str, stage: str) -> None:
        self.field_name = field_name
        self.stage = stage
        super().__init__(
            f"{field_name} cannot be changed once the shipment is {stage}; "
            "a support ticket is required"
        )


class SenderMayNotSetPlatformPolicy(OrderingError):
    """v6.3 p.13 Role boundary — Sender (MER-15)."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        super().__init__(
            "these are company-wide rules a sender cannot set: " + ", ".join(keys)
        )


# ------------------------------------------------------------------ pricing


class NotServiceable(OrderingError):
    def __init__(self, governorate: str) -> None:
        self.governorate = governorate
        super().__init__(f"HUDHUD does not currently deliver to {governorate}")


class TariffNotConfigured(OrderingError):
    """No rate covers this route.

    v6.3 publishes no tariff, so this service refuses to quote rather than inventing a
    price. Loading the approved rates makes quoting work with no code change.
    """

    def __init__(self, origin: str, destination: str) -> None:
        self.origin = origin
        self.destination = destination
        super().__init__(
            f"no delivery tariff is configured for {origin} to {destination}"
        )


# ------------------------------------------------------------------ concurrency


class StaleOrderingRecord(OrderingError):
    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"{table} changed concurrently")
