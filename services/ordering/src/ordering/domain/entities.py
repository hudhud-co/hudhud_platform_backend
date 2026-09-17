"""Ordering aggregates: order, shipment request, goods taxonomy, tariff."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ordering.domain.money import Money
from ordering.domain.value_objects import (
    REQUEST_TRANSITIONS,
    SELF_SERVICE_EDITABLE,
    CancellationReason,
    EditStage,
    OrderStatus,
    ParcelMeasurements,
    PaymentTerms,
    ReceiverDetails,
    RequestStatus,
    SenderKind,
    ShipmentAddOns,
)


@dataclass(frozen=True, slots=True)
class SenderRef:
    """Who is sending.

    A merchant sender always carries a merchant id; a customer sender never does. Keeping
    them in one value object rather than two nullable columns means an inconsistent pair
    cannot be constructed at all.
    """

    kind: SenderKind
    principal_id: UUID
    merchant_id: UUID | None = None
    store_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind is SenderKind.MERCHANT and self.merchant_id is None:
            msg = "a merchant sender must name its merchant"
            raise ValueError(msg)
        if self.kind is SenderKind.CUSTOMER and self.merchant_id is not None:
            msg = "a customer sender cannot belong to a merchant"
            raise ValueError(msg)

    @property
    def is_merchant(self) -> bool:
        return self.kind is SenderKind.MERCHANT


@dataclass(slots=True)
class Order:
    """The commercial agreement. One order can produce more than one shipment (p.14)."""

    order_id: UUID
    sender: SenderRef
    status: OrderStatus = OrderStatus.OPEN
    reference: str = ""
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    cancelled_at: datetime | None = None
    version: int = 1

    @property
    def is_open(self) -> bool:
        return self.status is OrderStatus.OPEN


@dataclass(slots=True)
class ShipmentRequest:
    """One physical parcel, from first keystroke to the moment Shipment takes over.

    This aggregate deliberately stops at ``REGISTERED``. Custody, checkpoint scans and the
    delivery outcome belong to Shipment, which is the sole lifecycle writer (ADR-0003);
    duplicating any of that here would create a second source of truth about where a
    parcel is.
    """

    request_id: UUID
    order_id: UUID
    sender: SenderRef
    tracking_code: str
    receiver: ReceiverDetails
    description: str
    status: RequestStatus = RequestStatus.DRAFT
    goods_category_code: str | None = None
    measurements: ParcelMeasurements = field(default_factory=ParcelMeasurements)
    payment_terms: PaymentTerms = PaymentTerms.PREPAID
    cod_amount: Money | None = None
    add_ons: ShipmentAddOns = field(default_factory=ShipmentAddOns)
    label_code: str | None = None
    label_linked_at: datetime | None = None
    pickup_store_id: UUID | None = None
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None
    assigned_courier_id: UUID | None = None
    delivery_fee: Money | None = None
    packaging_fee: Money | None = None
    tariff_reference: str | None = None
    created_at: datetime | None = None
    registered_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: CancellationReason | None = None
    #: Set by Shipment's custody fact, consumed through the inbox — never written locally
    #: from a request. It is what moves the edit stage to IN_CUSTODY.
    custody_started_at: datetime | None = None
    #: Set by Delivery's arrival fact, also through the inbox. SHP-12 turns on exactly
    #: this moment: once the courier is at the door, the COD amount is no longer changeable.
    courier_at_receiver_at: datetime | None = None
    version: int = 1

    # ------------------------------------------------------------- state

    def can_transition_to(self, target: RequestStatus) -> bool:
        return target in REQUEST_TRANSITIONS[self.status]

    @property
    def is_terminal(self) -> bool:
        return not REQUEST_TRANSITIONS[self.status]

    @property
    def has_label(self) -> bool:
        return self.label_code is not None

    @property
    def requires_label(self) -> bool:
        """Only a merchant labels a parcel.

        v6.3 p.8 and p.18: for a regular customer, "hub staff — not the customer — stick
        the label and scan it", so a customer request must never wait on one.
        """
        return self.sender.is_merchant

    @property
    def is_collectable(self) -> bool:
        return self.status is RequestStatus.READY_FOR_PICKUP

    @property
    def courier_at_the_door(self) -> bool:
        """SHP-12 — a change must reach the courier before they reach the receiver."""
        return self.courier_at_receiver_at is not None

    # ------------------------------------------------------------- editing

    @property
    def edit_stage(self) -> EditStage:
        if self.status in {RequestStatus.CANCELLED} or self.custody_started_at is not None:
            return (
                EditStage.CLOSED
                if self.status is RequestStatus.CANCELLED
                else EditStage.IN_CUSTODY
            )
        if self.status is RequestStatus.REGISTERED:
            return EditStage.IN_CUSTODY
        if self.assigned_courier_id is not None:
            return EditStage.COURIER_ASSIGNED
        return EditStage.UNASSIGNED

    def self_service_editable_fields(self) -> frozenset[str]:
        return SELF_SERVICE_EDITABLE[self.edit_stage]

    def may_edit(self, field_name: str) -> bool:
        return field_name in self.self_service_editable_fields()


@dataclass(slots=True)
class GoodsCategory:
    """A kind of goods the sender picks during send (MER-23).

    ``restriction_note`` carries the product's own warnings — the app shows "Needs a
    permit for some items" against medicine and "Air transport not allowed" against
    batteries. They are advisory text, not an invented acceptance rule.
    """

    code: str
    display_name: str
    hint: str | None = None
    restriction_note: str | None = None
    prohibited: bool = False
    sort_order: int = 0
    archived_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


@dataclass(slots=True)
class TariffRate:
    """One row of the delivery-fee tariff, in exact minor units.

    Rates are operational data an accountant loads, not a constant in code. v6.3 publishes
    no tariff, so a default here would be an invented price.
    """

    tariff_id: UUID
    reference: str
    origin_governorate: str
    destination_governorate: str
    delivery_fee: Money
    packaging_fee: Money
    effective_from: datetime
    effective_to: datetime | None = None
    version: int = 1

    def covers(self, moment: datetime) -> bool:
        if moment < self.effective_from:
            return False
        return self.effective_to is None or moment < self.effective_to
