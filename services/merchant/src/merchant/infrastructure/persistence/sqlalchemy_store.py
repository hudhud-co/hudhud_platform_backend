"""PostgreSQL unit of work for the Merchant service.

Writes are staged during the transaction and flushed on commit with a version-conditional
UPDATE, so a lost update surfaces as `StaleMerchantRecord` rather than silently winning.
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

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
from merchant.domain.errors import StaleMerchantRecord
from merchant.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from merchant.domain.value_objects import (
    OPEN_APPLICATION_STATUSES,
    ApplicationStatus,
    DeliveryFeePayer,
    GeoPoint,
    LabelStockSource,
    MembershipStatus,
    MerchantStatus,
    PrinterAuthorizationStatus,
    StockKind,
    TeamRole,
)
from merchant.infrastructure.persistence.models import (
    IntegrationInboxRow,
    IntegrationOutboxRow,
    LabelStockRow,
    MerchantApplicationRow,
    MerchantRow,
    PrinterAuthorizationRow,
    ProductCategoryRow,
    ProductRow,
    StandingPolicyRow,
    StoreRow,
    TeamMembershipRow,
)

_OPEN_STATUS_VALUES = tuple(status.value for status in OPEN_APPLICATION_STATUSES)
_LIVE_MEMBERSHIP_VALUES = (MembershipStatus.PENDING.value, MembershipStatus.ACTIVE.value)

_STAGES = (
    "applications",
    "merchants",
    "policies",
    "stores",
    "memberships",
    "label_stock",
    "printers",
    "products",
    "categories",
    "outbox",
    "inbox",
)


class SqlAlchemyMerchantUnitOfWork:
    """One instance serves every request, so its transaction is request-scoped.

    ``create_app`` builds this once and puts it on ``app.state``. Holding the
    open session on ``self`` meant two requests in flight shared it, and the
    second ``begin()`` raised ``transaction already active`` — a 500 for any two
    simultaneous users. ``_session`` and ``_pending`` are therefore properties
    over :class:`~contextvars.ContextVar`, which FastAPI gives a fresh copy of
    per request, so every existing call site keeps working unchanged.
    """

    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session_var: ContextVar[SaSession | None] = ContextVar(
            "merchant_session", default=None
        )
        self._pending_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "merchant_pending", default=None
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

    # --------------------------------------------------- request-scoped state

    @property
    def _session(self) -> SaSession | None:
        return self._session_var.get()

    @_session.setter
    def _session(self, value: SaSession | None) -> None:
        self._session_var.set(value)

    @property
    def _pending(self) -> dict[str, dict] | None:
        return self._pending_var.get()

    @_pending.setter
    def _pending(self, value: dict[str, dict] | None) -> None:
        self._pending_var.set(value)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entity, previous in pending["applications"].values():
            _versioned(
                session,
                MerchantApplicationRow,
                MerchantApplicationRow.application_id == entity.application_id,
                previous=previous,
                values=_application_values(entity),
                new_row=lambda e=entity: MerchantApplicationRow(
                    application_id=e.application_id, **_application_values(e)
                ),
            )
        for entity, previous in pending["merchants"].values():
            _versioned(
                session,
                MerchantRow,
                MerchantRow.merchant_id == entity.merchant_id,
                previous=previous,
                values=_merchant_values(entity),
                new_row=lambda e=entity: MerchantRow(
                    merchant_id=e.merchant_id, **_merchant_values(e)
                ),
            )
        for entity, previous in pending["policies"].values():
            _versioned(
                session,
                StandingPolicyRow,
                StandingPolicyRow.merchant_id == entity.merchant_id,
                previous=previous,
                values=_policy_values(entity),
                new_row=lambda e=entity: StandingPolicyRow(
                    merchant_id=e.merchant_id, **_policy_values(e)
                ),
            )
        for entity, previous in pending["stores"].values():
            _versioned(
                session,
                StoreRow,
                StoreRow.store_id == entity.store_id,
                previous=previous,
                values=_store_values(entity),
                new_row=lambda e=entity: StoreRow(
                    store_id=e.store_id, **_store_values(e)
                ),
            )
        for entity, previous in pending["memberships"].values():
            _versioned(
                session,
                TeamMembershipRow,
                TeamMembershipRow.membership_id == entity.membership_id,
                previous=previous,
                values=_membership_values(entity),
                new_row=lambda e=entity: TeamMembershipRow(
                    membership_id=e.membership_id, **_membership_values(e)
                ),
            )
        for entity, previous in pending["label_stock"].values():
            _versioned(
                session,
                LabelStockRow,
                LabelStockRow.allocation_id == entity.allocation_id,
                previous=previous,
                values=_allocation_values(entity),
                new_row=lambda e=entity: LabelStockRow(
                    allocation_id=e.allocation_id, **_allocation_values(e)
                ),
            )
        for entity, previous in pending["printers"].values():
            _versioned(
                session,
                PrinterAuthorizationRow,
                PrinterAuthorizationRow.authorization_id == entity.authorization_id,
                previous=previous,
                values=_printer_values(entity),
                new_row=lambda e=entity: PrinterAuthorizationRow(
                    authorization_id=e.authorization_id, **_printer_values(e)
                ),
            )
        for entity, previous in pending["categories"].values():
            _versioned(
                session,
                ProductCategoryRow,
                ProductCategoryRow.category_id == entity.category_id,
                previous=previous,
                values=_category_values(entity),
                new_row=lambda e=entity: ProductCategoryRow(
                    category_id=e.category_id, **_category_values(e)
                ),
            )
        for entity, previous in pending["products"].values():
            _versioned(
                session,
                ProductRow,
                ProductRow.product_id == entity.product_id,
                previous=previous,
                values=_product_values(entity),
                new_row=lambda e=entity: ProductRow(
                    product_id=e.product_id, **_product_values(e)
                ),
            )
        for record in pending["outbox"].values():
            session.add(IntegrationOutboxRow(**_outbox_values(record)))
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

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


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleMerchantRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyMerchantUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _ApplicationRepo(_Repo):
    def save(self, application: MerchantApplication) -> None:
        _stage(
            self._stage_for("applications"),
            application.application_id,
            application,
            application.version,
        )

    def get(self, application_id: UUID) -> MerchantApplication | None:
        staged = self._stage_for("applications").get(application_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(MerchantApplicationRow, application_id)
        return _application_entity(row) if row is not None else None

    def find_open_for_applicant(self, principal_id: UUID) -> MerchantApplication | None:
        row = (
            self._session()
            .execute(
                select(MerchantApplicationRow).where(
                    MerchantApplicationRow.applicant_principal_id == principal_id,
                    MerchantApplicationRow.status.in_(_OPEN_STATUS_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _application_entity(row) if row is not None else None

    def list_for_applicant(self, principal_id: UUID) -> tuple[MerchantApplication, ...]:
        rows = (
            self._session()
            .execute(
                select(MerchantApplicationRow).where(
                    MerchantApplicationRow.applicant_principal_id == principal_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_application_entity(row) for row in rows)

    def find_by_reference(self, reference: str) -> MerchantApplication | None:
        row = (
            self._session()
            .execute(
                select(MerchantApplicationRow).where(
                    MerchantApplicationRow.reference == reference
                )
            )
            .scalars()
            .first()
        )
        return _application_entity(row) if row is not None else None


class _MerchantRepo(_Repo):
    def save(self, merchant: Merchant) -> None:
        _stage(
            self._stage_for("merchants"), merchant.merchant_id, merchant, merchant.version
        )

    def get(self, merchant_id: UUID) -> Merchant | None:
        staged = self._stage_for("merchants").get(merchant_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(MerchantRow, merchant_id)
        return _merchant_entity(row) if row is not None else None

    def find_by_code(self, merchant_code: str) -> Merchant | None:
        for staged, _ in self._stage_for("merchants").values():
            if staged.merchant_code == merchant_code:
                return staged
        row = (
            self._session()
            .execute(select(MerchantRow).where(MerchantRow.merchant_code == merchant_code))
            .scalars()
            .first()
        )
        return _merchant_entity(row) if row is not None else None

    def find_by_owner(self, principal_id: UUID) -> Merchant | None:
        row = (
            self._session()
            .execute(
                select(MerchantRow).where(
                    MerchantRow.owner_principal_id == principal_id
                )
            )
            .scalars()
            .first()
        )
        return _merchant_entity(row) if row is not None else None

    def list_for_owner(self, principal_id: UUID) -> tuple[Merchant, ...]:
        """Every merchant this principal owns — a person may register several.

        Uses the same `ix_merchant_owner` index as :meth:`find_by_owner`; no schema
        change is involved.
        """
        rows = (
            self._session()
            .execute(
                select(MerchantRow).where(
                    MerchantRow.owner_principal_id == principal_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_merchant_entity(row) for row in rows)


class _PolicyRepo(_Repo):
    def save(self, policy: StandingShipmentPolicy) -> None:
        _stage(self._stage_for("policies"), policy.merchant_id, policy, policy.version)

    def get(self, merchant_id: UUID) -> StandingShipmentPolicy | None:
        staged = self._stage_for("policies").get(merchant_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(StandingPolicyRow, merchant_id)
        return _policy_entity(row) if row is not None else None


class _StoreRepo(_Repo):
    def save(self, store: Store) -> None:
        _stage(self._stage_for("stores"), store.store_id, store, store.version)

    def get(self, store_id: UUID) -> Store | None:
        staged = self._stage_for("stores").get(store_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(StoreRow, store_id)
        return _store_entity(row) if row is not None else None

    def list_for_merchant(
        self, merchant_id: UUID, *, include_archived: bool = False
    ) -> tuple[Store, ...]:
        stmt = select(StoreRow).where(StoreRow.merchant_id == merchant_id)
        if not include_archived:
            stmt = stmt.where(StoreRow.archived_at.is_(None))
        rows = self._session().execute(stmt).scalars().all()
        return tuple(_store_entity(row) for row in rows)

    def find_default_pickup(self, merchant_id: UUID) -> Store | None:
        row = (
            self._session()
            .execute(
                select(StoreRow).where(
                    StoreRow.merchant_id == merchant_id,
                    StoreRow.is_default_pickup.is_(True),
                    StoreRow.archived_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        return _store_entity(row) if row is not None else None


class _MembershipRepo(_Repo):
    def save(self, membership: TeamMembership) -> None:
        _stage(
            self._stage_for("memberships"),
            membership.membership_id,
            membership,
            membership.version,
        )

    def get(self, membership_id: UUID) -> TeamMembership | None:
        staged = self._stage_for("memberships").get(membership_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(TeamMembershipRow, membership_id)
        return _membership_entity(row) if row is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[TeamMembership, ...]:
        rows = (
            self._session()
            .execute(
                select(TeamMembershipRow).where(
                    TeamMembershipRow.merchant_id == merchant_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_membership_entity(row) for row in rows)

    def list_for_principal(self, principal_id: UUID) -> tuple[TeamMembership, ...]:
        rows = (
            self._session()
            .execute(
                select(TeamMembershipRow).where(
                    TeamMembershipRow.member_principal_id == principal_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_membership_entity(row) for row in rows)

    def find_live_for_phone(
        self, merchant_id: UUID, phone: str
    ) -> TeamMembership | None:
        row = (
            self._session()
            .execute(
                select(TeamMembershipRow).where(
                    TeamMembershipRow.merchant_id == merchant_id,
                    TeamMembershipRow.invited_phone == phone,
                    TeamMembershipRow.status.in_(_LIVE_MEMBERSHIP_VALUES),
                )
            )
            .scalars()
            .first()
        )
        return _membership_entity(row) if row is not None else None

    def list_pending_for_phone(self, phone: str) -> tuple[TeamMembership, ...]:
        rows = (
            self._session()
            .execute(
                select(TeamMembershipRow).where(
                    TeamMembershipRow.invited_phone == phone,
                    TeamMembershipRow.status == MembershipStatus.PENDING.value,
                )
            )
            .scalars()
            .all()
        )
        return tuple(_membership_entity(row) for row in rows)


class _LabelStockRepo(_Repo):
    def save(self, allocation: LabelStockAllocation) -> None:
        _stage(
            self._stage_for("label_stock"),
            allocation.allocation_id,
            allocation,
            allocation.version,
        )

    def get(self, allocation_id: UUID) -> LabelStockAllocation | None:
        staged = self._stage_for("label_stock").get(allocation_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(LabelStockRow, allocation_id)
        return _allocation_entity(row) if row is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[LabelStockAllocation, ...]:
        rows = (
            self._session()
            .execute(select(LabelStockRow).where(LabelStockRow.merchant_id == merchant_id))
            .scalars()
            .all()
        )
        return tuple(_allocation_entity(row) for row in rows)


class _PrinterRepo(_Repo):
    def save(self, authorization: PrinterAuthorization) -> None:
        _stage(
            self._stage_for("printers"),
            authorization.authorization_id,
            authorization,
            authorization.version,
        )

    def get(self, authorization_id: UUID) -> PrinterAuthorization | None:
        staged = self._stage_for("printers").get(authorization_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(PrinterAuthorizationRow, authorization_id)
        return _printer_entity(row) if row is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[PrinterAuthorization, ...]:
        rows = (
            self._session()
            .execute(
                select(PrinterAuthorizationRow).where(
                    PrinterAuthorizationRow.merchant_id == merchant_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_printer_entity(row) for row in rows)


class _CatalogueRepo(_Repo):
    def save_product(self, product: Product) -> None:
        _stage(
            self._stage_for("products"), product.product_id, product, product.version
        )

    def get_product(self, product_id: UUID) -> Product | None:
        staged = self._stage_for("products").get(product_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(ProductRow, product_id)
        return _product_entity(row) if row is not None else None

    def list_products(
        self, merchant_id: UUID, *, category_id: UUID | None = None
    ) -> tuple[Product, ...]:
        stmt = select(ProductRow).where(
            ProductRow.merchant_id == merchant_id, ProductRow.archived_at.is_(None)
        )
        if category_id is not None:
            stmt = stmt.where(ProductRow.category_id == category_id)
        rows = self._session().execute(stmt).scalars().all()
        return tuple(_product_entity(row) for row in rows)

    def save_category(self, category: ProductCategory) -> None:
        _stage(
            self._stage_for("categories"),
            category.category_id,
            category,
            category.version,
        )

    def get_category(self, category_id: UUID) -> ProductCategory | None:
        staged = self._stage_for("categories").get(category_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(ProductCategoryRow, category_id)
        return _category_entity(row) if row is not None else None

    def list_categories(self, merchant_id: UUID) -> tuple[ProductCategory, ...]:
        rows = (
            self._session()
            .execute(
                select(ProductCategoryRow).where(
                    ProductCategoryRow.merchant_id == merchant_id,
                    ProductCategoryRow.archived_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        return tuple(_category_entity(row) for row in rows)

    def count_products_in_category(self, category_id: UUID) -> int:
        return int(
            self._session()
            .execute(
                select(func.count())
                .select_from(ProductRow)
                .where(
                    ProductRow.category_id == category_id,
                    ProductRow.archived_at.is_(None),
                )
            )
            .scalar_one()
        )


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        self._stage_for("outbox")[record.event_id] = record

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        staged = self._stage_for("outbox").get(event_id)
        if staged is not None:
            return staged
        row = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.event_id == event_id
                )
            )
            .scalars()
            .first()
        )
        return _outbox_entity(row) if row is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.status == OutboxStatus.PENDING.value
                )
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.aggregate_id == aggregate_id
                )
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (record, True)

    def save(self, record: InboxRecord) -> None:
        stage = self._stage_for("inbox")
        key = (record.consumer_name, record.event_id)
        is_new = stage[key][1] if key in stage else False
        stage[key] = (record, is_new)


# ------------------------------------------------------------------ mappers


def _application_values(e: MerchantApplication) -> dict[str, object]:
    return {
        "applicant_principal_id": e.applicant_principal_id,
        "reference": e.reference,
        "status": e.status.value,
        "attributes": dict(e.attributes),
        "created_at": e.created_at,
        "submitted_at": e.submitted_at,
        "decided_at": e.decided_at,
        "decided_by_principal_id": e.decided_by_principal_id,
        "decision_reason": e.decision_reason,
        "version": e.version,
    }


def _application_entity(row: MerchantApplicationRow) -> MerchantApplication:
    return MerchantApplication(
        application_id=row.application_id,  # type: ignore[arg-type]
        applicant_principal_id=row.applicant_principal_id,  # type: ignore[arg-type]
        reference=row.reference,
        status=ApplicationStatus(row.status),
        attributes=dict(row.attributes or {}),
        created_at=row.created_at,  # type: ignore[arg-type]
        submitted_at=row.submitted_at,  # type: ignore[arg-type]
        decided_at=row.decided_at,  # type: ignore[arg-type]
        decided_by_principal_id=row.decided_by_principal_id,  # type: ignore[arg-type]
        decision_reason=row.decision_reason,
        version=row.version,
    )


def _merchant_values(e: Merchant) -> dict[str, object]:
    return {
        "owner_principal_id": e.owner_principal_id,
        "merchant_code": e.merchant_code,
        "display_name": e.display_name,
        "status": e.status.value,
        "application_id": e.application_id,
        "activated_at": e.activated_at,
        "version": e.version,
    }


def _merchant_entity(row: MerchantRow) -> Merchant:
    return Merchant(
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        owner_principal_id=row.owner_principal_id,  # type: ignore[arg-type]
        merchant_code=row.merchant_code,
        display_name=row.display_name,
        status=MerchantStatus(row.status),
        application_id=row.application_id,  # type: ignore[arg-type]
        activated_at=row.activated_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _policy_values(e: StandingShipmentPolicy) -> dict[str, object]:
    return {
        "open_box_allowed": e.open_box_allowed,
        "photo_documentation_enabled": e.photo_documentation_enabled,
        "hudhud_packaging_enabled": e.hudhud_packaging_enabled,
        "delivery_fee_payer": e.delivery_fee_payer.value,
        "updated_at": e.updated_at,
        "version": e.version,
    }


def _policy_entity(row: StandingPolicyRow) -> StandingShipmentPolicy:
    return StandingShipmentPolicy(
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        open_box_allowed=bool(row.open_box_allowed),
        photo_documentation_enabled=bool(row.photo_documentation_enabled),
        hudhud_packaging_enabled=bool(row.hudhud_packaging_enabled),
        delivery_fee_payer=DeliveryFeePayer(row.delivery_fee_payer),
        updated_at=row.updated_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _store_values(e: Store) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "name": e.name,
        "governorate": e.governorate,
        "address_line": e.address_line,
        "area": e.area,
        "landmark": e.landmark,
        "latitude": e.geo.latitude if e.geo else None,
        "longitude": e.geo.longitude if e.geo else None,
        "is_default_pickup": e.is_default_pickup,
        "created_at": e.created_at,
        "archived_at": e.archived_at,
        "version": e.version,
    }


def _store_entity(row: StoreRow) -> Store:
    geo = None
    if row.latitude is not None and row.longitude is not None:
        geo = GeoPoint(
            latitude=Decimal(str(row.latitude)), longitude=Decimal(str(row.longitude))
        )
    return Store(
        store_id=row.store_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        name=row.name,
        governorate=row.governorate,
        address_line=row.address_line,
        area=row.area,
        landmark=row.landmark,
        geo=geo,
        is_default_pickup=bool(row.is_default_pickup),
        created_at=row.created_at,  # type: ignore[arg-type]
        archived_at=row.archived_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _membership_values(e: TeamMembership) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "invited_phone": e.invited_phone,
        "display_name": e.display_name,
        "role": e.role.value,
        "status": e.status.value,
        "store_ids": list(e.store_ids),
        "member_principal_id": e.member_principal_id,
        "invited_at": e.invited_at,
        "accepted_at": e.accepted_at,
        "ended_at": e.ended_at,
        "version": e.version,
    }


def _membership_entity(row: TeamMembershipRow) -> TeamMembership:
    return TeamMembership(
        membership_id=row.membership_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        invited_phone=row.invited_phone,
        role=TeamRole(row.role),
        status=MembershipStatus(row.status),
        store_ids=tuple(row.store_ids or ()),
        display_name=row.display_name,
        member_principal_id=row.member_principal_id,  # type: ignore[arg-type]
        invited_at=row.invited_at,  # type: ignore[arg-type]
        accepted_at=row.accepted_at,  # type: ignore[arg-type]
        ended_at=row.ended_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _allocation_values(e: LabelStockAllocation) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "batch_reference": e.batch_reference,
        "source": e.source.value,
        "stock_kind": e.stock_kind.value,
        "label_count": e.label_count,
        "consumed_count": e.consumed_count,
        "printer_authorization_id": e.printer_authorization_id,
        "issued_at": e.issued_at,
        "version": e.version,
    }


def _allocation_entity(row: LabelStockRow) -> LabelStockAllocation:
    return LabelStockAllocation(
        allocation_id=row.allocation_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        batch_reference=row.batch_reference,
        source=LabelStockSource(row.source),
        stock_kind=StockKind(row.stock_kind),
        label_count=row.label_count,
        consumed_count=row.consumed_count,
        printer_authorization_id=row.printer_authorization_id,  # type: ignore[arg-type]
        issued_at=row.issued_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _printer_values(e: PrinterAuthorization) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "printer_serial": e.printer_serial,
        "stock_reference": e.stock_reference,
        "status": e.status.value,
        "issued_at": e.issued_at,
        "revoked_at": e.revoked_at,
        "version": e.version,
    }


def _printer_entity(row: PrinterAuthorizationRow) -> PrinterAuthorization:
    return PrinterAuthorization(
        authorization_id=row.authorization_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        printer_serial=row.printer_serial,
        stock_reference=row.stock_reference,
        status=PrinterAuthorizationStatus(row.status),
        issued_at=row.issued_at,  # type: ignore[arg-type]
        revoked_at=row.revoked_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _category_values(e: ProductCategory) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "name": e.name,
        "created_at": e.created_at,
        "archived_at": e.archived_at,
        "version": e.version,
    }


def _category_entity(row: ProductCategoryRow) -> ProductCategory:
    return ProductCategory(
        category_id=row.category_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        name=row.name,
        created_at=row.created_at,  # type: ignore[arg-type]
        archived_at=row.archived_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _product_values(e: Product) -> dict[str, object]:
    return {
        "merchant_id": e.merchant_id,
        "name": e.name,
        "category_id": e.category_id,
        "description": e.description,
        "created_at": e.created_at,
        "archived_at": e.archived_at,
        "version": e.version,
    }


def _product_entity(row: ProductRow) -> Product:
    return Product(
        product_id=row.product_id,  # type: ignore[arg-type]
        merchant_id=row.merchant_id,  # type: ignore[arg-type]
        name=row.name,
        category_id=row.category_id,  # type: ignore[arg-type]
        description=row.description,
        created_at=row.created_at,  # type: ignore[arg-type]
        archived_at=row.archived_at,  # type: ignore[arg-type]
        version=row.version,
    )


def _outbox_values(e: OutboxRecord) -> dict[str, object]:
    return {
        "id": e.id,
        "event_id": e.event_id,
        "subject": e.subject,
        "event_type": e.event_type,
        "event_version": e.event_version,
        "aggregate_id": e.aggregate_id,
        "aggregate_version": e.aggregate_version,
        "payload_json": e.payload_json,
        "status": e.status.value,
        "attempt_count": e.attempt_count,
        "max_attempts": e.max_attempts,
        "next_attempt_at": e.next_attempt_at,
        "processing_owner": e.processing_owner,
        "processing_until": e.processing_until,
        "published_at": e.published_at,
        "last_error_code": e.last_error_code,
        "last_error_message": e.last_error_message,
        "created_at": e.created_at,
    }


def _outbox_entity(row: IntegrationOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,  # type: ignore[arg-type]
        event_id=row.event_id,  # type: ignore[arg-type]
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,  # type: ignore[arg-type]
        aggregate_version=row.aggregate_version,
        payload_json=dict(row.payload_json or {}),
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,  # type: ignore[arg-type]
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,  # type: ignore[arg-type]
        published_at=row.published_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,  # type: ignore[arg-type]
    )


def _inbox_values(e: InboxRecord) -> dict[str, object]:
    return {"inbox_id": e.inbox_id, **_inbox_update_values(e), **_inbox_key_values(e)}


def _inbox_key_values(e: InboxRecord) -> dict[str, object]:
    return {"consumer_name": e.consumer_name, "event_id": e.event_id}


def _inbox_update_values(e: InboxRecord) -> dict[str, object]:
    return {
        "event_type": e.event_type,
        "event_version": e.event_version,
        "status": e.status.value,
        "received_at": e.received_at,
        "attempt_count": e.attempt_count,
        "processing_started_at": e.processing_started_at,
        "processing_lease_until": e.processing_lease_until,
        "processed_at": e.processed_at,
        "last_error_code": e.last_error_code,
        "payload_json": e.payload_json,
    }


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,  # type: ignore[arg-type]
        consumer_name=row.consumer_name,
        event_id=row.event_id,  # type: ignore[arg-type]
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,  # type: ignore[arg-type]
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,  # type: ignore[arg-type]
        processing_lease_until=row.processing_lease_until,  # type: ignore[arg-type]
        processed_at=row.processed_at,  # type: ignore[arg-type]
        last_error_code=row.last_error_code,
        payload_json=dict(row.payload_json or {}),
    )
