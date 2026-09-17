"""In-memory Merchant unit of work for unit tests.

Snapshot-per-transaction: ``begin`` deep-copies, ``commit`` swaps, ``rollback`` discards.
That makes a rolled-back command genuinely invisible, which is what the tests about
partial failure actually need to assert.
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from uuid import UUID

from merchant.domain.entities import (
    LabelStockAllocation,
    Merchant,
    MerchantApplication,
    PrinterAuthorization,
    Product,
    ProductCategory,
    StandingShipmentPolicy,
    Store,
    TeamMembership,
)
from merchant.domain.messaging import InboxRecord, OutboxRecord
from merchant.domain.value_objects import (
    OPEN_APPLICATION_STATUSES,
    MembershipStatus,
)

_COLLECTIONS = (
    "applications",
    "merchants",
    "policies",
    "stores",
    "memberships",
    "label_stock",
    "printer_authorizations",
    "products",
    "categories",
    "outbox",
    "inbox",
)

_LIVE_MEMBERSHIP = frozenset({MembershipStatus.PENDING, MembershipStatus.ACTIVE})


class InMemoryMerchantUnitOfWork:
    def __init__(self) -> None:
        self._committed: dict[str, dict] = {name: {} for name in _COLLECTIONS}
        self._tx_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "merchant_memory_tx", default=None
        )
        self._applications = _ApplicationRepo(self)
        self._merchants = _MerchantRepo(self)
        self._policies = _PolicyRepo(self)
        self._stores = _StoreRepo(self)
        self._memberships = _MembershipRepo(self)
        self._label_stock = _LabelStockRepo(self)
        self._printers = _PrinterRepo(self)
        self._catalogue = _CatalogueRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    @property
    def _tx(self) -> dict[str, dict] | None:
        return self._tx_var.get()

    def begin(self) -> None:
        if self._tx_var.get() is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._tx_var.set(copy.deepcopy(self._committed))

    def commit(self) -> None:
        tx = self._tx_var.get()
        if tx is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._committed = tx
        self._tx_var.set(None)

    def rollback(self) -> None:
        self._tx_var.set(None)

    def _working(self, name: str) -> dict:
        return self._tx[name] if self._tx is not None else self._committed[name]

    @property
    def applications(self) -> _ApplicationRepo:
        return self._applications

    @property
    def merchants(self) -> _MerchantRepo:
        return self._merchants

    @property
    def policies(self) -> _PolicyRepo:
        return self._policies

    @property
    def stores(self) -> _StoreRepo:
        return self._stores

    @property
    def memberships(self) -> _MembershipRepo:
        return self._memberships

    @property
    def label_stock(self) -> _LabelStockRepo:
        return self._label_stock

    @property
    def printer_authorizations(self) -> _PrinterRepo:
        return self._printers

    @property
    def catalogue(self) -> _CatalogueRepo:
        return self._catalogue

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


class _Repo:
    def __init__(self, store: InMemoryMerchantUnitOfWork) -> None:
        self._store = store


class _ApplicationRepo(_Repo):
    def save(self, application: MerchantApplication) -> None:
        self._store._working("applications")[application.application_id] = copy.deepcopy(
            application
        )

    def get(self, application_id: UUID) -> MerchantApplication | None:
        found = self._store._working("applications").get(application_id)
        return copy.deepcopy(found) if found is not None else None

    def find_open_for_applicant(self, principal_id: UUID) -> MerchantApplication | None:
        for application in self._store._working("applications").values():
            if (
                application.applicant_principal_id == principal_id
                and application.status in OPEN_APPLICATION_STATUSES
            ):
                return copy.deepcopy(application)
        return None

    def list_for_applicant(self, principal_id: UUID) -> tuple[MerchantApplication, ...]:
        return tuple(
            copy.deepcopy(application)
            for application in self._store._working("applications").values()
            if application.applicant_principal_id == principal_id
        )

    def find_by_reference(self, reference: str) -> MerchantApplication | None:
        for application in self._store._working("applications").values():
            if application.reference == reference:
                return copy.deepcopy(application)
        return None


class _MerchantRepo(_Repo):
    def save(self, merchant: Merchant) -> None:
        self._store._working("merchants")[merchant.merchant_id] = copy.deepcopy(merchant)

    def get(self, merchant_id: UUID) -> Merchant | None:
        found = self._store._working("merchants").get(merchant_id)
        return copy.deepcopy(found) if found is not None else None

    def find_by_code(self, merchant_code: str) -> Merchant | None:
        for merchant in self._store._working("merchants").values():
            if merchant.merchant_code == merchant_code:
                return copy.deepcopy(merchant)
        return None

    def find_by_owner(self, principal_id: UUID) -> Merchant | None:
        for merchant in self._store._working("merchants").values():
            if merchant.owner_principal_id == principal_id:
                return copy.deepcopy(merchant)
        return None

    def list_for_owner(self, principal_id: UUID) -> tuple[Merchant, ...]:
        return tuple(
            copy.deepcopy(merchant)
            for merchant in self._store._working("merchants").values()
            if merchant.owner_principal_id == principal_id
        )


class _PolicyRepo(_Repo):
    def save(self, policy: StandingShipmentPolicy) -> None:
        self._store._working("policies")[policy.merchant_id] = copy.deepcopy(policy)

    def get(self, merchant_id: UUID) -> StandingShipmentPolicy | None:
        found = self._store._working("policies").get(merchant_id)
        return copy.deepcopy(found) if found is not None else None


class _StoreRepo(_Repo):
    def save(self, store: Store) -> None:
        self._store._working("stores")[store.store_id] = copy.deepcopy(store)

    def get(self, store_id: UUID) -> Store | None:
        found = self._store._working("stores").get(store_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_merchant(
        self, merchant_id: UUID, *, include_archived: bool = False
    ) -> tuple[Store, ...]:
        return tuple(
            copy.deepcopy(store)
            for store in self._store._working("stores").values()
            if store.merchant_id == merchant_id and (include_archived or store.is_active)
        )

    def find_default_pickup(self, merchant_id: UUID) -> Store | None:
        for store in self._store._working("stores").values():
            if (
                store.merchant_id == merchant_id
                and store.is_default_pickup
                and store.is_active
            ):
                return copy.deepcopy(store)
        return None


class _MembershipRepo(_Repo):
    def save(self, membership: TeamMembership) -> None:
        self._store._working("memberships")[membership.membership_id] = copy.deepcopy(
            membership
        )

    def get(self, membership_id: UUID) -> TeamMembership | None:
        found = self._store._working("memberships").get(membership_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[TeamMembership, ...]:
        return tuple(
            copy.deepcopy(membership)
            for membership in self._store._working("memberships").values()
            if membership.merchant_id == merchant_id
        )

    def list_for_principal(self, principal_id: UUID) -> tuple[TeamMembership, ...]:
        return tuple(
            copy.deepcopy(membership)
            for membership in self._store._working("memberships").values()
            if membership.member_principal_id == principal_id
        )

    def find_live_for_phone(
        self, merchant_id: UUID, phone: str
    ) -> TeamMembership | None:
        for membership in self._store._working("memberships").values():
            if (
                membership.merchant_id == merchant_id
                and membership.invited_phone == phone
                and membership.status in _LIVE_MEMBERSHIP
            ):
                return copy.deepcopy(membership)
        return None

    def list_pending_for_phone(self, phone: str) -> tuple[TeamMembership, ...]:
        return tuple(
            copy.deepcopy(membership)
            for membership in self._store._working("memberships").values()
            if membership.invited_phone == phone
            and membership.status is MembershipStatus.PENDING
        )


class _LabelStockRepo(_Repo):
    def save(self, allocation: LabelStockAllocation) -> None:
        self._store._working("label_stock")[allocation.allocation_id] = copy.deepcopy(
            allocation
        )

    def get(self, allocation_id: UUID) -> LabelStockAllocation | None:
        found = self._store._working("label_stock").get(allocation_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[LabelStockAllocation, ...]:
        return tuple(
            copy.deepcopy(allocation)
            for allocation in self._store._working("label_stock").values()
            if allocation.merchant_id == merchant_id
        )


class _PrinterRepo(_Repo):
    def save(self, authorization: PrinterAuthorization) -> None:
        self._store._working("printer_authorizations")[
            authorization.authorization_id
        ] = copy.deepcopy(authorization)

    def get(self, authorization_id: UUID) -> PrinterAuthorization | None:
        found = self._store._working("printer_authorizations").get(authorization_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[PrinterAuthorization, ...]:
        return tuple(
            copy.deepcopy(authorization)
            for authorization in self._store._working("printer_authorizations").values()
            if authorization.merchant_id == merchant_id
        )


class _CatalogueRepo(_Repo):
    def save_product(self, product: Product) -> None:
        self._store._working("products")[product.product_id] = copy.deepcopy(product)

    def get_product(self, product_id: UUID) -> Product | None:
        found = self._store._working("products").get(product_id)
        return copy.deepcopy(found) if found is not None else None

    def list_products(
        self, merchant_id: UUID, *, category_id: UUID | None = None
    ) -> tuple[Product, ...]:
        return tuple(
            copy.deepcopy(product)
            for product in self._store._working("products").values()
            if product.merchant_id == merchant_id
            and product.is_active
            and (category_id is None or product.category_id == category_id)
        )

    def save_category(self, category: ProductCategory) -> None:
        self._store._working("categories")[category.category_id] = copy.deepcopy(category)

    def get_category(self, category_id: UUID) -> ProductCategory | None:
        found = self._store._working("categories").get(category_id)
        return copy.deepcopy(found) if found is not None else None

    def list_categories(self, merchant_id: UUID) -> tuple[ProductCategory, ...]:
        return tuple(
            copy.deepcopy(category)
            for category in self._store._working("categories").values()
            if category.merchant_id == merchant_id and category.is_active
        )

    def count_products_in_category(self, category_id: UUID) -> int:
        return sum(
            1
            for product in self._store._working("products").values()
            if product.category_id == category_id and product.is_active
        )


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        rows = self._store._working("outbox")
        if record.event_id in rows:
            msg = f"duplicate outbox event_id: {record.event_id}"
            raise ValueError(msg)
        for existing in rows.values():
            if (
                existing.aggregate_id == record.aggregate_id
                and existing.aggregate_version == record.aggregate_version
            ):
                msg = (
                    "duplicate outbox aggregate version: "
                    f"{record.aggregate_id}@{record.aggregate_version}"
                )
                raise ValueError(msg)
        rows[record.event_id] = copy.deepcopy(record)

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        found = self._store._working("outbox").get(event_id)
        return copy.deepcopy(found) if found is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.status.value == "pending"
        )

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            copy.deepcopy(record)
            for record in self._store._working("outbox").values()
            if record.aggregate_id == aggregate_id
        )


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        found = self._store._working("inbox").get((consumer_name, event_id))
        return copy.deepcopy(found) if found is not None else None

    def insert(self, record: InboxRecord) -> None:
        rows = self._store._working("inbox")
        key = (record.consumer_name, record.event_id)
        if key in rows:
            msg = f"duplicate inbox delivery: {key}"
            raise ValueError(msg)
        rows[key] = copy.deepcopy(record)

    def save(self, record: InboxRecord) -> None:
        self._store._working("inbox")[
            (record.consumer_name, record.event_id)
        ] = copy.deepcopy(record)
