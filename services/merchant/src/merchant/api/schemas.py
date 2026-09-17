"""Request and response models for the Merchant HTTP adapter."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ application


class StartApplicationRequest(_Model):
    #: An open bag on purpose — v6.3 p.10 has not decided the field list (MER-02).
    attributes: dict[str, Any] = Field(default_factory=dict)


class UpdateApplicationRequest(_Model):
    attributes: dict[str, Any]


class DecideApplicationRequest(_Model):
    decision: str = Field(pattern="^(APPROVED|CHANGES_REQUESTED)$")
    reason: str | None = Field(default=None, max_length=2000)
    display_name: str | None = Field(default=None, max_length=160)
    merchant_code: str | None = Field(default=None, max_length=16)


class ApplicationResponse(_Model):
    application_id: UUID
    reference: str
    status: str
    attributes: dict[str, Any]
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    version: int


class ApplicationRequirementsResponse(_Model):
    """What an applicant must supply, and whether that has been decided at all."""

    submission_enabled: bool
    required_attributes: list[str]
    blocked_reason: str | None = None


# ------------------------------------------------------------------ merchant


class MerchantResponse(_Model):
    merchant_id: UUID
    merchant_code: str
    display_name: str
    status: str
    activated_at: datetime | None = None
    version: int


class PolicyResponse(_Model):
    merchant_id: UUID
    open_box_allowed: bool
    photo_documentation_enabled: bool
    hudhud_packaging_enabled: bool
    delivery_fee_payer: str
    platform_fixed: dict[str, Any]
    version: int


class UpdatePolicyRequest(BaseModel):
    """Extra keys are accepted here and then refused with a named error.

    Silently ignoring an attempt to set a company-wide rule would let a client believe it
    had changed one (MER-15), so the unknown keys are collected and reported.
    """

    model_config = ConfigDict(extra="allow")

    open_box_allowed: bool | None = None
    photo_documentation_enabled: bool | None = None
    hudhud_packaging_enabled: bool | None = None
    delivery_fee_payer: str | None = Field(default=None, pattern="^(SENDER|RECEIVER)$")

    def extra_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.model_extra or {}))


# ------------------------------------------------------------------ store


class GeoPointModel(_Model):
    latitude: Decimal
    longitude: Decimal


class CreateStoreRequest(_Model):
    name: str = Field(min_length=1, max_length=160)
    governorate: str = Field(min_length=1, max_length=32)
    address_line: str = Field(min_length=1, max_length=512)
    area: str | None = Field(default=None, max_length=160)
    landmark: str | None = Field(default=None, max_length=256)
    geo: GeoPointModel | None = None
    make_default_pickup: bool = False


class UpdateStoreRequest(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    governorate: str | None = Field(default=None, max_length=32)
    address_line: str | None = Field(default=None, min_length=1, max_length=512)
    area: str | None = Field(default=None, max_length=160)
    landmark: str | None = Field(default=None, max_length=256)
    geo: GeoPointModel | None = None


class StoreResponse(_Model):
    store_id: UUID
    merchant_id: UUID
    name: str
    governorate: str
    address_line: str
    area: str | None = None
    landmark: str | None = None
    geo: GeoPointModel | None = None
    is_default_pickup: bool
    archived_at: datetime | None = None
    version: int


# ------------------------------------------------------------------ team


class InviteMemberRequest(_Model):
    phone: str = Field(min_length=4, max_length=32)
    store_ids: list[UUID] = Field(min_length=1)
    display_name: str | None = Field(default=None, max_length=160)


class UpdateBranchesRequest(_Model):
    store_ids: list[UUID] = Field(min_length=1)


class RespondToInviteRequest(_Model):
    accept: bool


class MembershipResponse(_Model):
    membership_id: UUID
    merchant_id: UUID
    #: Only the last four digits ever leave the service — the rest is the invitee's PII.
    phone_last4: str
    display_name: str | None = None
    role: str
    status: str
    store_ids: list[UUID]
    permissions: list[str]
    invited_at: datetime | None = None
    accepted_at: datetime | None = None
    version: int


class MyMerchantResponse(_Model):
    """One store the signed-in principal may act for.

    ``relationship`` is ``OWNER`` or ``MEMBER``. An owner's reach comes from ownership, so
    ``role`` is null and ``permissions`` is empty; a member reports the exact capability
    strings the client must gate on, never a role label alone.
    """

    merchant_id: UUID
    merchant_code: str
    display_name: str
    status: str
    relationship: str
    role: str | None = None
    permissions: list[str] = Field(default_factory=list)
    store_ids: list[UUID] = Field(default_factory=list)


class StoreAccessResponse(_Model):
    merchant_id: UUID
    principal_id: UUID
    role: str
    permissions: list[str]
    store_ids: list[UUID]


# ------------------------------------------------------------------ labels


class IssueStockRequest(_Model):
    batch_reference: str = Field(min_length=1, max_length=64)
    label_count: int = Field(ge=1, le=100000)
    source: str = Field(
        default="HUDHUD_PREPRINTED",
        pattern="^(HUDHUD_PREPRINTED|MERCHANT_SELF_PRINTED)$",
    )
    stock_kind: str = Field(default="LABEL", pattern="^(LABEL|PACKAGING_SEAL)$")
    printer_authorization_id: UUID | None = None


class ConsumeLabelsRequest(_Model):
    count: int = Field(default=1, ge=1, le=10000)


class AuthorizePrinterRequest(_Model):
    printer_serial: str = Field(min_length=1, max_length=64)
    stock_reference: str = Field(min_length=1, max_length=64)


class AllocationResponse(_Model):
    allocation_id: UUID
    batch_reference: str
    source: str
    stock_kind: str
    label_count: int
    consumed_count: int
    remaining: int
    printer_authorization_id: UUID | None = None
    version: int


class StockSummaryResponse(_Model):
    merchant_id: UUID
    total_labels: int
    consumed_labels: int
    remaining: int
    total_seals: int
    consumed_seals: int
    remaining_seals: int


class PrinterAuthorizationResponse(_Model):
    authorization_id: UUID
    merchant_id: UUID
    printer_serial: str
    stock_reference: str
    status: str
    version: int


# ------------------------------------------------------------------ catalogue


class CreateCategoryRequest(_Model):
    name: str = Field(min_length=1, max_length=120)


class CreateProductRequest(_Model):
    name: str = Field(min_length=1, max_length=160)
    category_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2000)


class UpdateProductRequest(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2000)


class CategoryResponse(_Model):
    category_id: UUID
    merchant_id: UUID
    name: str
    version: int


class ProductResponse(_Model):
    product_id: UUID
    merchant_id: UUID
    name: str
    category_id: UUID | None = None
    description: str | None = None
    version: int
