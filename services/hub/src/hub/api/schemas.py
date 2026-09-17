"""Request and response models for the Hub HTTP adapter."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ hubs


class RegisterHubRequest(_Model):
    code: str = Field(min_length=2, max_length=12)
    name: str = Field(min_length=1, max_length=160)
    governorate: str = Field(min_length=2, max_length=32)
    #: v6.3 p.23 — per hub, never company-wide.
    cut_off_local_time: time
    vehicle_cameras_fitted: bool = False


class SetCutOffRequest(_Model):
    cut_off_local_time: time


class HubResponse(_Model):
    hub_id: UUID
    code: str
    name: str
    governorate: str
    cut_off_local_time: time
    is_active: bool
    vehicle_cameras_fitted: bool
    version: int


# ------------------------------------------------------------------ drop-off


class ExpectDropOffRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")
    shipment_request_id: UUID | None = None
    sender_principal_id: UUID | None = None


class CaptureDetailsRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")
    details: dict[str, str]
    sender_principal_id: UUID | None = None


class ApplyLabelRequest(_Model):
    label_code: str = Field(min_length=6, max_length=32)
    weight_grams: int = Field(gt=0)


class AcceptDropOffRequest(_Model):
    destination_governorate: str | None = Field(default=None, max_length=32)


class DropOffResponse(_Model):
    drop_off_id: UUID
    hub_id: UUID
    tracking_code: str
    status: str
    weight_grams: int | None = None
    label_code: str | None = None
    expires_at: datetime | None = None
    accepted_at: datetime | None = None
    version: int


# ------------------------------------------------------------------ processing


class ScanInRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")
    destination_governorate: str = Field(min_length=2, max_length=32)


class SortRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")
    urgency: str = Field(default="STANDARD", pattern="^(STANDARD|URGENT)$")
    route_code: str | None = Field(default=None, max_length=32)


class HoldRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")
    reason: str = Field(
        pattern="^(TAMPER_INVESTIGATION|CHECKPOINT_INTERCEPTION|DAMAGED_IN_HUB"
        "|MISSING_AT_RECONCILIATION|OPERATIONS_HOLD)$"
    )
    consignment_id: UUID | None = None


class HandoverRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")


class PresenceResponse(_Model):
    presence_id: UUID
    hub_id: UUID
    tracking_code: str
    status: str
    destination_governorate: str
    urgency: str
    routing_decision: str | None = None
    route_code: str | None = None
    consignment_id: UUID | None = None
    hold_reason: str | None = None
    disposition: str | None = None
    version: int


class HoldResponse(_Model):
    parcel: PresenceResponse
    disposition: str


class ActivityResponse(_Model):
    hub_id: UUID
    received: int
    sorted: int
    grouped: int
    ready_for_last_mile: int
    held: int
    awaiting_linehaul: int
    delayed_linehauls: int
    backlog: int


# ------------------------------------------------------------------ consignment


class OpenConsignmentRequest(_Model):
    destination_hub_id: UUID


class AddParcelRequest(_Model):
    tracking_code: str = Field(pattern=r"^SHP-\d{8}-\d{6}$")


class SealRequest(_Model):
    seal_code: str = Field(min_length=6, max_length=32)


class DispatchRequest(_Model):
    override_cut_off: bool = False


class SealCheckRequest(_Model):
    #: ``null`` means the seal was not there at all — a tamper outcome, not a pass.
    observed_seal_code: str | None = Field(default=None, max_length=32)


class ConsignmentResponse(_Model):
    consignment_id: UUID
    origin_hub_id: UUID
    destination_hub_id: UUID
    status: str
    seal_code: str | None = None
    parcel_codes: list[str]
    parcel_count: int
    version: int


class SealCheckResponse(_Model):
    check_id: UUID
    consignment_id: UUID
    outcome: str
    opened_investigation: bool
    consignment: ConsignmentResponse


# ------------------------------------------------------------------ linehaul


class PlanLinehaulRequest(_Model):
    vehicle_reference: str = Field(min_length=1, max_length=64)
    driver_principal_id: UUID
    planned_departure_at: datetime | None = None
    expected_arrival_at: datetime | None = None


class PositionRequest(_Model):
    source: str = Field(pattern="^(VEHICLE_TRACKER|DRIVER_DEVICE)$")
    latitude: Decimal
    longitude: Decimal
    recorded_at: datetime | None = None


class DeviationRequest(_Model):
    note: str = Field(min_length=1, max_length=512)
    revised_arrival_at: datetime | None = None


class LinehaulResponse(_Model):
    linehaul_id: UUID
    consignment_id: UUID
    origin_hub_id: UUID
    destination_hub_id: UUID
    vehicle_reference: str
    status: str
    departed_at: datetime | None = None
    expected_arrival_at: datetime | None = None
    arrived_at: datetime | None = None
    route_deviation_flagged: bool
    deviation_note: str | None = None
    version: int


class PositionResponse(_Model):
    position_id: UUID
    linehaul_id: UUID
    source: str
    latitude: Decimal
    longitude: Decimal
    recorded_at: datetime
