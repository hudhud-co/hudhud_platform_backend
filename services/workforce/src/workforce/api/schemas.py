"""Request and response models for the Workforce HTTP adapter."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShiftWindowModel(_Model):
    weekday: int = Field(ge=1, le=7)
    slot: str = Field(pattern="^(MORNING|AFTERNOON|EVENING)$")
    starts_at: time
    ends_at: time


class VehicleModel(_Model):
    kind: str = Field(min_length=1, max_length=32)
    plate_number: str = Field(min_length=3, max_length=16)
    model: str | None = Field(default=None, max_length=64)


class SubmitApplicationRequest(_Model):
    full_name: str = Field(min_length=1, max_length=160)
    vehicle: VehicleModel
    declared_shifts: list[ShiftWindowModel] = Field(min_length=1)
    terms_version: str = Field(min_length=1, max_length=32)
    documents_received: list[str] = Field(default_factory=list)


class VerifyApplicationRequest(_Model):
    office: str = Field(min_length=1, max_length=160)
    documents_verified: list[str] = Field(min_length=1)


class RejectApplicationRequest(_Model):
    reason: str = Field(min_length=1, max_length=512)


class ApplicationResponse(_Model):
    application_id: UUID
    reference: str
    status: str
    full_name: str
    awaits_office_visit: bool
    documents_received: list[str]
    documents_verified: list[str]
    verified_at_office: str | None = None
    decision_reason: str | None = None
    version: int


class DriverResponse(_Model):
    driver_id: UUID
    principal_id: UUID
    full_name: str
    vehicle: VehicleModel
    status: str
    version: int


class SetShiftPatternRequest(_Model):
    windows: list[ShiftWindowModel] = Field(min_length=1)


class ShiftPatternResponse(_Model):
    pattern_id: UUID
    driver_id: UUID
    windows: list[ShiftWindowModel]
    effective_from: date
    effective_to: date | None = None
    version: int


class AttendanceResponse(_Model):
    attendance_id: UUID
    shift_date: date
    scheduled_start: time
    status: str
    started_at: datetime | None = None
    lateness_minutes: int


class BlockResponse(_Model):
    block_id: UUID
    driver_id: UUID
    reason: str
    shift_date: date
    scheduled_start: time
    delay_minutes: int
    blocked_since: datetime
    unblock_requested: bool
    cleared_at: datetime | None = None
    clearing_note: str | None = None


class StartShiftResponse(_Model):
    attendance: AttendanceResponse
    block: BlockResponse | None = None
    #: True when a pending or approved leave request stopped a penalty being applied.
    penalty_waived_by_leave: bool


class ClearBlockRequest(_Model):
    note: str | None = Field(default=None, max_length=512)


class RequestLeaveRequest(_Model):
    reason: str = Field(pattern="^(SICK|FAMILY_EMERGENCY|VEHICLE_PROBLEM|PERSONAL)$")
    kind: str = Field(pattern="^(FULL_DAY|HOURLY)$")
    start_date: date
    end_date: date
    starts_at: time | None = None
    ends_at: time | None = None
    note: str | None = Field(default=None, max_length=1024)


class DecideLeaveRequest(_Model):
    approve: bool
    note: str | None = Field(default=None, max_length=1024)


class LeaveResponse(_Model):
    leave_id: UUID
    reference: str
    reason: str
    kind: str
    start_date: date
    end_date: date
    starts_at: time | None = None
    ends_at: time | None = None
    status: str
    note: str | None = None
    decision_note: str | None = None
    version: int


class EligibilityResponse(_Model):
    """The answer Pickup and Delivery consume, with its reasons."""

    principal_id: UUID
    driver_id: UUID | None = None
    eligible: bool
    reasons: list[str]
    shift_started: bool
