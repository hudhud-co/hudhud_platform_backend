"""Request and response models for the Notification HTTP adapter."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


_CATEGORY = "^(ACCEPTANCE|PRE_DELIVERY|STATUS_CHANGE|SEAL_CONFIRMATION|OPERATIONS_BULLETIN)$"
_CHANNEL = "^(SMS|APP|WHATSAPP|PHONE_CALL)$"


class SetPreferenceRequest(_Model):
    category: str = Field(pattern=_CATEGORY)
    channel: str = Field(pattern=_CHANNEL)
    enabled: bool


class PreferenceResponse(_Model):
    preference_id: UUID
    category: str
    channel: str
    enabled: bool
    #: True when this combination can never be switched off (NTF-03).
    mandatory: bool
    version: int


class CentreEntryResponse(_Model):
    entry_id: UUID
    category: str
    title: str
    body: str
    tracking_code: str | None = None
    created_at: datetime | None = None
    read: bool


class CentreResponse(_Model):
    unread: int
    entries: list[CentreEntryResponse]


class BulletinRequest(_Model):
    """Operations reaching drivers (NTF-09)."""

    reference: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=512)
    driver_phones: list[str] = Field(min_length=1, max_length=500)
    driver_principal_ids: list[UUID] = Field(default_factory=list)


class BulletinResponse(_Model):
    planned: int
    suppressed: int
    unreachable: int


class DeliveryStatusResponse(_Model):
    """What went out for one parcel. Never includes a body or a delivery code."""

    tracking_code: str
    notifications: list[NotificationSummary]


class NotificationSummary(_Model):
    notification_id: UUID
    audience: str
    channel: str
    category: str
    template_code: str
    status: str
    attempt_count: int
    failure_reason: str | None = None
    sent_at: datetime | None = None


DeliveryStatusResponse.model_rebuild()
