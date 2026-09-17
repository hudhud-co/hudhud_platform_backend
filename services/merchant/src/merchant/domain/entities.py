"""Merchant aggregates: application, merchant, store, team, label stock, catalogue."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from merchant.domain.value_objects import (
    APPLICATION_TRANSITIONS,
    ROLE_PERMISSIONS,
    ApplicationStatus,
    DeliveryFeePayer,
    GeoPoint,
    LabelStockSource,
    MembershipStatus,
    MerchantStatus,
    PrinterAuthorizationStatus,
    StockKind,
    StorePermission,
    TeamRole,
)


@dataclass(slots=True)
class MerchantApplication:
    """A regular customer's request for merchant status (v6.3 p.9).

    ``attributes`` is deliberately an open bag. v6.3 p.10 records the exact data set as an
    open item — "This has not yet been defined and needs a deliberate answer before the
    application flow can be built" — so this service stores whatever the applicant
    supplies and refuses to *submit* until an operator has configured which attributes are
    required. Baking a field list in here would fabricate a KYC policy (MER-02).
    """

    application_id: UUID
    applicant_principal_id: UUID
    reference: str
    status: ApplicationStatus
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by_principal_id: UUID | None = None
    decision_reason: str | None = None
    version: int = 1

    @property
    def is_open(self) -> bool:
        return bool(APPLICATION_TRANSITIONS[self.status])

    def can_transition_to(self, target: ApplicationStatus) -> bool:
        return target in APPLICATION_TRANSITIONS[self.status]


@dataclass(slots=True)
class StandingShipmentPolicy:
    """What a merchant sets once and every new shipment inherits (v6.3 p.13).

    Every add-on defaults to off. v6.3 p.13 makes open-box "off by default" explicit, and
    p.14 makes photo documentation opt-in — "Without this add-on, no photo is taken at any
    stage". Defaulting either to on would silently authorise photographing parcels.
    """

    merchant_id: UUID
    open_box_allowed: bool = False
    photo_documentation_enabled: bool = False
    hudhud_packaging_enabled: bool = False
    delivery_fee_payer: DeliveryFeePayer = DeliveryFeePayer.SENDER
    updated_at: datetime | None = None
    version: int = 1


@dataclass(slots=True)
class Merchant:
    """An approved merchant. Created only by approving an application."""

    merchant_id: UUID
    owner_principal_id: UUID
    merchant_code: str
    display_name: str
    status: MerchantStatus = MerchantStatus.ACTIVE
    application_id: UUID | None = None
    activated_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.status is MerchantStatus.ACTIVE


@dataclass(slots=True)
class Store:
    """A store branch and the place a courier collects from (MER-16).

    ``is_default_pickup`` is enforced as at-most-one per merchant by a partial unique
    index, so a pickup can never be scheduled against an ambiguous branch.
    """

    store_id: UUID
    merchant_id: UUID
    name: str
    governorate: str
    address_line: str
    area: str | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None
    is_default_pickup: bool = False
    created_at: datetime | None = None
    archived_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


@dataclass(slots=True)
class TeamMembership:
    """One person's membership of one merchant's team (MER-20, SEC-05).

    A membership is created against a *phone number*, not a principal: the invitation
    "goes to this number's HUDHUD account" and the invitee may not have opened the app
    yet. ``member_principal_id`` is filled in when they accept, which is also the moment
    any permission first exists.
    """

    membership_id: UUID
    merchant_id: UUID
    invited_phone: str
    role: TeamRole
    status: MembershipStatus
    store_ids: tuple[UUID, ...]
    display_name: str | None = None
    member_principal_id: UUID | None = None
    invited_at: datetime | None = None
    accepted_at: datetime | None = None
    ended_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.status is MembershipStatus.ACTIVE

    @property
    def permissions(self) -> frozenset[StorePermission]:
        """A pending, declined or removed membership carries no permission at all."""
        if not self.is_active:
            return frozenset()
        return ROLE_PERMISSIONS[self.role]

    def covers_store(self, store_id: UUID) -> bool:
        """A member sees only the branches they are assigned to."""
        return self.is_active and store_id in self.store_ids


@dataclass(slots=True)
class PrinterAuthorization:
    """Permission for one merchant to self-print on Hudhud-supplied equipment (MER-04).

    v6.3 p.12: a merchant may print their own labels "but only using a specific printer
    and blank label stock that Hudhud provides. A merchant may not use any other printer
    or label stock for this."
    """

    authorization_id: UUID
    merchant_id: UUID
    printer_serial: str
    stock_reference: str
    status: PrinterAuthorizationStatus = PrinterAuthorizationStatus.ACTIVE
    issued_at: datetime | None = None
    revoked_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.status is PrinterAuthorizationStatus.ACTIVE


@dataclass(slots=True)
class LabelStockAllocation:
    """A batch of barcoded consumables issued to a merchant (MER-03, MER-12).

    Stock exists *before* a parcel does. v6.3 p.10: registering a shipment means the
    merchant "takes one of their pre-printed labels and sticks it onto the parcel" — so
    this service issues and counts stock and never mints a code for a specific shipment.

    ``stock_kind`` separates barcode labels from the per-parcel packaging seals of the
    Hudhud-packaging add-on (p.14), which are the same shape of inventory with different
    rules: seals are never self-printed, and are scanned again at delivery.
    """

    allocation_id: UUID
    merchant_id: UUID
    batch_reference: str
    source: LabelStockSource
    label_count: int
    stock_kind: StockKind = StockKind.LABEL
    consumed_count: int = 0
    printer_authorization_id: UUID | None = None
    issued_at: datetime | None = None
    version: int = 1

    @property
    def remaining(self) -> int:
        return self.label_count - self.consumed_count

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0


@dataclass(slots=True)
class ProductCategory:
    """A merchant's own category for saved products (MER-17)."""

    category_id: UUID
    merchant_id: UUID
    name: str
    created_at: datetime | None = None
    archived_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


@dataclass(slots=True)
class Product:
    """A saved product, reusable as shipment contents (MER-17)."""

    product_id: UUID
    merchant_id: UUID
    name: str
    category_id: UUID | None = None
    description: str | None = None
    created_at: datetime | None = None
    archived_at: datetime | None = None
    version: int = 1

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


@dataclass(frozen=True, slots=True)
class StoreAccess:
    """The answer other services need: what may this principal do in this merchant?

    Published as a read model over memberships so that Ordering and Finance never have to
    read Merchant's tables to decide whether a warehouse keeper may act.
    """

    merchant_id: UUID
    principal_id: UUID
    role: TeamRole
    permissions: frozenset[StorePermission]
    store_ids: tuple[UUID, ...]
