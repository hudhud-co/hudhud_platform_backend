"""Shared builders for Merchant tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from merchant.application.application_service import (
    ApplicationPolicy,
    MerchantApplicationService,
)
from merchant.application.catalogue_service import CatalogueService
from merchant.application.inbox_service import InboxService
from merchant.application.label_stock_service import LabelStockService
from merchant.application.store_service import StoreDraft, StoreService
from merchant.application.team_service import InviteDraft, TeamService
from merchant.domain.entities import Merchant, Store
from merchant.infrastructure.memory import InMemoryMerchantUnitOfWork
from merchant.ports.authorization import MerchantActor, MerchantRole

APPLICANT = UUID("11111111-1111-4111-8111-111111111111")
REVIEWER = UUID("22222222-2222-4222-8222-222222222222")
MEMBER = UUID("33333333-3333-4333-8333-333333333333")
OUTSIDER = UUID("44444444-4444-4444-8444-444444444444")

#: A configured required-field list, standing in for the decision MER-02 is waiting on.
#: Tests use it to prove the *flow* works; they never assert these are the real fields.
CONFIGURED_ATTRIBUTES = ("business_name", "business_type", "business_phone", "city")


def build_store() -> InMemoryMerchantUnitOfWork:
    return InMemoryMerchantUnitOfWork()


def application_service(
    uow: InMemoryMerchantUnitOfWork,
    *,
    required_attributes: tuple[str, ...] = CONFIGURED_ATTRIBUTES,
) -> MerchantApplicationService:
    return MerchantApplicationService(
        uow, policy=ApplicationPolicy(required_attributes=required_attributes)
    )


def undecided_application_service(
    uow: InMemoryMerchantUnitOfWork,
) -> MerchantApplicationService:
    """The shipped configuration: MER-02 unresolved, so submission is refused."""
    return MerchantApplicationService(uow, policy=ApplicationPolicy())


def store_service(uow: InMemoryMerchantUnitOfWork) -> StoreService:
    return StoreService(uow)


def team_service(uow: InMemoryMerchantUnitOfWork) -> TeamService:
    return TeamService(uow)


def label_service(uow: InMemoryMerchantUnitOfWork) -> LabelStockService:
    return LabelStockService(uow)


def catalogue_service(uow: InMemoryMerchantUnitOfWork) -> CatalogueService:
    return CatalogueService(uow)


def inbox_service(uow: InMemoryMerchantUnitOfWork, **kwargs) -> InboxService:
    return InboxService(uow, **kwargs)


def valid_attributes() -> dict[str, str]:
    return {
        "business_name": "Karbala Home Goods",
        "business_type": "RETAIL",
        "business_phone": "+9647701234567",
        "city": "KARBALA",
    }


def approved_merchant(
    uow: InMemoryMerchantUnitOfWork, *, applicant: UUID = APPLICANT
) -> Merchant:
    """Walk the real application flow to an approved merchant — never hand-build one."""
    service = application_service(uow)
    application = service.start_application(
        applicant_principal_id=applicant, attributes=valid_attributes()
    )
    service.submit(application_id=application.application_id)
    result = service.approve(
        application_id=application.application_id,
        reviewer_principal_id=REVIEWER,
        display_name="Karbala Home Goods",
    )
    return result.merchant


def a_store(
    uow: InMemoryMerchantUnitOfWork, merchant: Merchant, *, name: str = "Center"
) -> Store:
    return store_service(uow).create_store(
        merchant_id=merchant.merchant_id,
        draft=StoreDraft(
            name=name, governorate="KARBALA", address_line="Al-Nuqabat street 8"
        ),
    )


def an_invite(store_ids: tuple[UUID, ...], *, phone: str = "+9647709998888") -> InviteDraft:
    return InviteDraft(phone=phone, store_ids=store_ids, display_name="Ali Hassan")


def owner_actor(principal_id: UUID = APPLICANT) -> MerchantActor:
    return MerchantActor(
        principal_id=principal_id, roles=frozenset({MerchantRole.MERCHANT_OWNER})
    )


def customer_actor(principal_id: UUID = APPLICANT) -> MerchantActor:
    return MerchantActor(
        principal_id=principal_id, roles=frozenset({MerchantRole.CUSTOMER})
    )


def operations_actor(principal_id: UUID = REVIEWER) -> MerchantActor:
    return MerchantActor(
        principal_id=principal_id, roles=frozenset({MerchantRole.OPERATIONS})
    )


def new_id() -> UUID:
    return uuid4()
