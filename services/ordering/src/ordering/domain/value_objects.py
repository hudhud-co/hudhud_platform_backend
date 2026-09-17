"""Value objects for orders, shipment requests, pricing and serviceability."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ordering.domain.money import Money


class SenderKind(StrEnum):
    """Who is sending, and therefore which rules apply.

    This is the single most consequential distinction in the ordering context. v6.3 p.8
    makes two Confirmed decisions about a regular customer that do not apply to a
    merchant: **no COD** and **no pickup**. Modelling the sender kind explicitly means
    those rules are enforced once, in the domain, instead of being remembered at each
    call site.
    """

    CUSTOMER = "CUSTOMER"
    MERCHANT = "MERCHANT"


class PaymentTerms(StrEnum):
    """v6.3 p.12: prepaid, postpaid or COD, which "determines what happens at the door"."""

    PREPAID = "PREPAID"
    POSTPAID = "POSTPAID"
    CASH_ON_DELIVERY = "CASH_ON_DELIVERY"


class DeliveryFeePayer(StrEnum):
    SENDER = "SENDER"
    RECEIVER = "RECEIVER"


class RequestStatus(StrEnum):
    """The ordering half of a parcel's life, before Shipment takes custody.

    Ordering never writes custody. ``REGISTERED`` is the last status this service owns:
    it means the parcel is fully described, labelled where required, and handed to the
    Shipment context, which is the sole lifecycle writer from that point (ADR-0003).
    """

    DRAFT = "DRAFT"
    #: Merchant path: details entered, waiting for a pre-printed label to be scanned on.
    AWAITING_LABEL = "AWAITING_LABEL"
    #: Merchant path: labelled and collectable — a pickup may now be booked.
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    #: Customer path: nothing to label — the customer carries it to a hub (v6.3 p.8).
    AWAITING_DROPOFF = "AWAITING_DROPOFF"
    REGISTERED = "REGISTERED"
    CANCELLED = "CANCELLED"


#: Permitted transitions. Anything absent is refused rather than quietly allowed.
REQUEST_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    RequestStatus.DRAFT: frozenset(
        {
            RequestStatus.AWAITING_LABEL,
            RequestStatus.AWAITING_DROPOFF,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.AWAITING_LABEL: frozenset(
        {RequestStatus.READY_FOR_PICKUP, RequestStatus.CANCELLED}
    ),
    RequestStatus.READY_FOR_PICKUP: frozenset(
        {RequestStatus.REGISTERED, RequestStatus.CANCELLED, RequestStatus.AWAITING_LABEL}
    ),
    RequestStatus.AWAITING_DROPOFF: frozenset(
        {RequestStatus.REGISTERED, RequestStatus.CANCELLED}
    ),
    #: Terminal for this context — Shipment owns the parcel from here.
    RequestStatus.REGISTERED: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}

#: Statuses at which nothing has entered Hudhud custody, so cancelling is free (SHP-10).
CANCELLABLE_STATUSES: frozenset[RequestStatus] = frozenset(
    {
        RequestStatus.DRAFT,
        RequestStatus.AWAITING_LABEL,
        RequestStatus.READY_FOR_PICKUP,
        RequestStatus.AWAITING_DROPOFF,
    }
)


class OrderStatus(StrEnum):
    """An order is the commercial agreement; each shipment is a physical parcel (p.14)."""

    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"


class EditStage(StrEnum):
    """How much of a shipment may still be changed, and by whom (SHP-11).

    Derived from where the parcel is, never stored as an independent flag — a stored copy
    would drift from the truth the moment a courier was assigned.
    """

    #: "No courier is assigned yet, so everything here can still be changed."
    UNASSIGNED = "UNASSIGNED"
    #: "The courier has been released. Confirm your changes and we book a new one."
    COURIER_ASSIGNED = "COURIER_ASSIGNED"
    #: Collected and moving: support applies corrections before the delivery attempt.
    IN_CUSTODY = "IN_CUSTODY"
    #: Delivered, returned or cancelled: nothing left to change.
    CLOSED = "CLOSED"


#: Fields a sender may change themselves at each stage. Everything else needs support.
SELF_SERVICE_EDITABLE: dict[EditStage, frozenset[str]] = {
    EditStage.UNASSIGNED: frozenset(
        {
            "receiver",
            "description",
            "goods_category",
            "measurements",
            "payment_terms",
            "cod_amount",
            "add_ons",
            "pickup_address",
            "pickup_window",
        }
    ),
    # Changing where or when a courier should come releases the one already booked.
    EditStage.COURIER_ASSIGNED: frozenset(
        {
            "receiver",
            "description",
            "goods_category",
            "measurements",
            "payment_terms",
            "cod_amount",
            "add_ons",
            "pickup_address",
            "pickup_window",
        }
    ),
    # "The parcel is already collected and moving in our network." Contents, dimensions
    # and the pickup address are fixed facts about a box someone is already carrying.
    EditStage.IN_CUSTODY: frozenset(),
    EditStage.CLOSED: frozenset(),
}

#: Changing either of these at COURIER_ASSIGNED releases the assigned courier.
COURIER_RELEASING_FIELDS: frozenset[str] = frozenset({"pickup_address", "pickup_window"})

#: Corrections support can still apply once the parcel is in the network, per
#: ``editIntroCollected``: the receiver, the address, and — for a merchant — the amount
#: to collect. Contents and dimensions are not on this list at any stage.
SUPPORT_CORRECTABLE_IN_CUSTODY: frozenset[str] = frozenset(
    {"receiver", "delivery_address", "cod_amount"}
)


class CancellationReason(StrEnum):
    SENDER_CHANGED_MIND = "SENDER_CHANGED_MIND"
    DUPLICATE = "DUPLICATE"
    PROHIBITED_CONTENTS = "PROHIBITED_CONTENTS"
    NOT_DROPPED_IN_TIME = "NOT_DROPPED_IN_TIME"
    OPERATIONS_DECISION = "OPERATIONS_DECISION"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A map pin. Optional — v6.3 p.12 keeps precise location optional."""

    latitude: Decimal
    longitude: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("-90") <= self.latitude <= Decimal("90")):
            msg = "latitude must be between -90 and 90"
            raise ValueError(msg)
        if not (Decimal("-180") <= self.longitude <= Decimal("180")):
            msg = "longitude must be between -180 and 180"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ReceiverDetails:
    """What the sender provides about the receiver (v6.3 p.12).

    Phone and governorate are mandatory — "the minimum Hudhud needs to reach and route to
    the receiver". Name, address and pin are optional: "helpful for the driver, but not
    required to register a shipment". Requiring more here would block real shipments the
    business has decided to accept.
    """

    phone: str
    governorate: str
    name: str | None = None
    address_line: str | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None

    @property
    def phone_last4(self) -> str:
        return self.phone[-4:]


@dataclass(frozen=True, slots=True)
class ParcelMeasurements:
    """Weight and size — **optional** since v6.3 (p.12, Confirmed decision).

    "Weight and size are optional. What is required instead is that the sender defines the
    parcel." The description carries that requirement; this does not.
    """

    weight_grams: int | None = None
    length_cm: int | None = None
    width_cm: int | None = None
    height_cm: int | None = None

    def __post_init__(self) -> None:
        for name in ("weight_grams", "length_cm", "width_cm", "height_cm"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                msg = f"{name} must be positive when given"
                raise ValueError(msg)

    @property
    def is_empty(self) -> bool:
        return all(
            getattr(self, name) is None
            for name in ("weight_grams", "length_cm", "width_cm", "height_cm")
        )


@dataclass(frozen=True, slots=True)
class ShipmentAddOns:
    """Per-shipment add-ons, inherited from the merchant's standing policy (MER-14).

    All three default off, matching v6.3 p.13 ("off by default") and p.14 ("Without this
    add-on, no photo is taken at any stage").
    """

    open_box_allowed: bool = False
    photo_documentation: bool = False
    hudhud_packaging: bool = False
    delivery_fee_payer: DeliveryFeePayer = DeliveryFeePayer.SENDER


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """A quoted delivery fee, in exact minor units."""

    delivery_fee: Money
    packaging_fee: Money
    tariff_reference: str

    @property
    def total(self) -> Money:
        return self.delivery_fee + self.packaging_fee


#: Iraqi governorates — the routing unit v6.3 requires (p.12).
GOVERNORATES: frozenset[str] = frozenset(
    {
        "BAGHDAD", "BASRA", "NINEVEH", "ERBIL", "SULAYMANIYAH", "DUHOK", "KIRKUK",
        "NAJAF", "KARBALA", "BABIL", "WASIT", "MAYSAN", "DHI_QAR", "MUTHANNA",
        "QADISIYYAH", "DIYALA", "ANBAR", "SALAH_AL_DIN", "HALABJA",
    }
)

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_TRACKING_CODE = re.compile(r"^SHP-\d{8}-\d{6}$")
_LABEL_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,31}$")


def normalize_governorate(raw: str) -> str:
    token = raw.strip().upper().replace(" ", "_").replace("-", "_")
    if token not in GOVERNORATES:
        msg = f"unknown governorate: {raw}"
        raise ValueError(msg)
    return token


def normalize_phone(raw: str) -> str:
    """Normalise to E.164, expanding the Iraqi local form (07XX…)."""
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


def is_tracking_code(value: str) -> bool:
    return bool(_TRACKING_CODE.match(value))


def normalize_label_code(raw: str) -> str:
    """A scanned label code. Drivers and merchants scan these; nobody types one."""
    candidate = raw.strip().upper()
    if not _LABEL_CODE.match(candidate):
        msg = f"not a usable label code: {raw}"
        raise ValueError(msg)
    return candidate
