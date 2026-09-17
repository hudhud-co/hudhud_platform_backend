"""Shared builders for Customer tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from customer.application.address_book_service import AddressBookService
from customer.application.profile_service import CustomerProfileService, LegalPolicy
from customer.infrastructure.memory import InMemoryCustomerUnitOfWork
from customer.ports.authorization import CustomerActor, CustomerRole

BASE_TIME = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
PRINCIPAL = UUID("11111111-1111-4111-8111-111111111111")
OTHER_PRINCIPAL = UUID("22222222-2222-4222-8222-222222222222")


def minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


def build_store() -> InMemoryCustomerUnitOfWork:
    return InMemoryCustomerUnitOfWork()


def profile_service(
    store: InMemoryCustomerUnitOfWork, *, policy: LegalPolicy | None = None
) -> CustomerProfileService:
    return CustomerProfileService(store, legal_policy=policy or LegalPolicy())


def address_service(store: InMemoryCustomerUnitOfWork) -> AddressBookService:
    return AddressBookService(store)


def customer_actor(principal_id: UUID = PRINCIPAL) -> CustomerActor:
    return CustomerActor(
        principal_id=principal_id, roles=frozenset({CustomerRole.CUSTOMER})
    )


def support_actor() -> CustomerActor:
    return CustomerActor(principal_id=uuid4(), roles=frozenset({CustomerRole.SUPPORT}))
