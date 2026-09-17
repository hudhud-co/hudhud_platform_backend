"""Customer request and response models. No request carries actor identity."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SetDisplayNameRequest(_Frozen):
    display_name: str = Field(min_length=1, max_length=120)


class AcceptLegalRequest(_Frozen):
    kind: str = Field(min_length=1, max_length=32)
    document_version: str = Field(min_length=1, max_length=32)


class NotificationPreferencesRequest(_Frozen):
    channels: list[str] = Field(default_factory=list, max_length=8)


class OutstandingDocument(_Frozen):
    kind: str
    required_version: str


class ProfileResponse(_Frozen):
    principal_id: UUID
    display_name: str | None
    completion_state: str
    notification_channels: list[str]
    outstanding_documents: list[OutstandingDocument]
    can_use_the_app: bool
    version: int
    idempotent_replay: bool = False


class CreateContactRequest(_Frozen):
    phone: str = Field(min_length=4, max_length=32)
    governorate: str = Field(min_length=2, max_length=32)
    display_name: str | None = Field(default=None, max_length=120)


class ContactResponse(_Frozen):
    contact_id: UUID
    phone: str
    governorate: str
    display_name: str | None
    archived_at: datetime | None = None


class GeoPointModel(_Frozen):
    latitude: Decimal
    longitude: Decimal


class CreateAddressRequest(_Frozen):
    kind: str = Field(default="DELIVERY", max_length=16)
    governorate: str = Field(min_length=2, max_length=32)
    line: str = Field(min_length=1, max_length=512)
    contact_id: UUID | None = None
    landmark: str | None = Field(default=None, max_length=256)
    geo: GeoPointModel | None = None
    make_default: bool = False


class AddressResponse(_Frozen):
    address_id: UUID
    kind: str
    governorate: str
    line: str
    contact_id: UUID | None
    landmark: str | None
    geo: GeoPointModel | None
    is_default: bool
    archived_at: datetime | None = None
