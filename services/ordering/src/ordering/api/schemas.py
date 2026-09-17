"""Request and response models for the Ordering HTTP adapter.

Money crosses the wire as an integer plus a currency, never as a decimal or a formatted
string: a JSON number with a fraction is a float somewhere, and a formatted string invites
parsing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyModel(_Model):
    minor_units: int = Field(ge=0)
    currency: str = Field(default="IQD", pattern="^IQD$")


class GeoPointModel(_Model):
    latitude: Decimal
    longitude: Decimal


# ------------------------------------------------------------------ orders


class OpenOrderRequest(_Model):
    """A merchant order names its merchant; a customer order names nothing."""

    merchant_id: UUID | None = None
    store_id: UUID | None = None


class OrderResponse(_Model):
    order_id: UUID
    reference: str
    sender_kind: str
    status: str
    version: int


# ------------------------------------------------------------------ shipments


class ReceiverModel(_Model):
    phone: str = Field(min_length=4, max_length=32)
    governorate: str = Field(min_length=2, max_length=32)
    name: str | None = Field(default=None, max_length=160)
    address_line: str | None = Field(default=None, max_length=512)
    landmark: str | None = Field(default=None, max_length=256)


class MeasurementsModel(_Model):
    """All optional — v6.3 p.12 made weight and size optional."""

    weight_grams: int | None = Field(default=None, gt=0)
    length_cm: int | None = Field(default=None, gt=0)
    width_cm: int | None = Field(default=None, gt=0)
    height_cm: int | None = Field(default=None, gt=0)


class AddOnsModel(_Model):
    open_box_allowed: bool = False
    photo_documentation: bool = False
    hudhud_packaging: bool = False
    delivery_fee_payer: str = Field(default="SENDER", pattern="^(SENDER|RECEIVER)$")


class ShipmentDraftModel(_Model):
    receiver: ReceiverModel
    description: str = Field(min_length=1, max_length=512)
    goods_category_code: str | None = Field(default=None, max_length=64)
    measurements: MeasurementsModel | None = None
    payment_terms: str = Field(
        default="PREPAID", pattern="^(PREPAID|POSTPAID|CASH_ON_DELIVERY)$"
    )
    cod_amount: MoneyModel | None = None
    add_ons: AddOnsModel | None = None
    pickup_store_id: UUID | None = None
    prohibited_goods_acknowledged: bool = False


class BulkShipmentRequest(_Model):
    shipments: list[ShipmentDraftModel] = Field(min_length=1, max_length=500)


class ShipmentResponse(_Model):
    request_id: UUID
    order_id: UUID
    tracking_code: str
    status: str
    sender_kind: str
    description: str
    goods_category_code: str | None = None
    receiver_governorate: str
    #: Only the last four digits — the full number is not a response field anywhere.
    receiver_phone_last4: str
    receiver_name: str | None = None
    payment_terms: str
    cod_amount: MoneyModel | None = None
    open_box_allowed: bool
    photo_documentation: bool
    hudhud_packaging: bool
    delivery_fee_payer: str
    label_code: str | None = None
    requires_label: bool
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None
    edit_stage: str
    editable_fields: list[str]
    version: int


class LinkLabelRequest(_Model):
    label_code: str = Field(min_length=4, max_length=32)


class BookPickupRequest(_Model):
    window_start: datetime
    window_end: datetime
    store_id: UUID | None = None


class PickupReadinessResponse(_Model):
    order_id: UUID
    labelled: int
    total: int
    pickup_available: bool
    blocked_reason: str | None = None


class AmendShipmentRequest(BaseModel):
    """Extra keys are accepted and then refused by name.

    Silently dropping an attempt to set a company-wide rule would let a client believe it
    had changed one (MER-15).
    """

    model_config = ConfigDict(extra="allow")

    receiver: ReceiverModel | None = None
    description: str | None = Field(default=None, min_length=1, max_length=512)
    goods_category_code: str | None = Field(default=None, max_length=64)
    add_ons: AddOnsModel | None = None
    pickup_store_id: UUID | None = None
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None

    def extra_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.model_extra or {}))


class AmendmentResponse(_Model):
    shipment: ShipmentResponse
    courier_released: bool
    changed_fields: list[str]


class ChangeCodRequest(_Model):
    amount: MoneyModel


class SupportCorrectionRequest(_Model):
    receiver: ReceiverModel | None = None
    cod_amount: MoneyModel | None = None


class CancelShipmentRequest(_Model):
    reason: str = Field(
        pattern="^(SENDER_CHANGED_MIND|DUPLICATE|PROHIBITED_CONTENTS"
        "|NOT_DROPPED_IN_TIME|OPERATIONS_DECISION)$"
    )


# ------------------------------------------------------------------ pricing


class QuoteResponse(_Model):
    origin_governorate: str
    destination_governorate: str
    delivery_fee: MoneyModel
    packaging_fee: MoneyModel
    total: MoneyModel
    tariff_reference: str


class PublishRateRequest(_Model):
    reference: str = Field(min_length=1, max_length=64)
    origin_governorate: str = Field(min_length=2, max_length=32)
    destination_governorate: str = Field(min_length=2, max_length=32)
    delivery_fee: MoneyModel
    packaging_fee: MoneyModel
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class TariffRateResponse(_Model):
    tariff_id: UUID
    reference: str
    origin_governorate: str
    destination_governorate: str
    delivery_fee: MoneyModel
    packaging_fee: MoneyModel
    effective_from: datetime
    effective_to: datetime | None = None


class ServiceabilityResponse(_Model):
    configured: bool
    governorates: list[str]


# ------------------------------------------------------------------ catalogue


class GoodsCategoryResponse(_Model):
    code: str
    display_name: str
    hint: str | None = None
    restriction_note: str | None = None
    prohibited: bool


class ProhibitedNoticeResponse(_Model):
    summary: str
    detail: str


# ------------------------------------------------------------------ public


class PublicTrackingResponse(_Model):
    """Everything a stranger holding a tracking code may see, and nothing else."""

    tracking_code: str
    status: str
    destination_governorate: str
    created_at: datetime | None = None
    registered_at: datetime | None = None
