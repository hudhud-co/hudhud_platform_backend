"""Persistence ports for the Customer service."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from customer.domain.entities import Address, Contact, CustomerProfile, LegalAcceptance
from customer.domain.value_objects import AddressKind


class ProfileRepository(Protocol):
    def save(self, profile: CustomerProfile) -> None: ...

    def get(self, principal_id: UUID) -> CustomerProfile | None: ...


class LegalAcceptanceRepository(Protocol):
    def save(self, acceptance: LegalAcceptance) -> None: ...

    def list_for_principal(self, principal_id: UUID) -> tuple[LegalAcceptance, ...]: ...

    def find(
        self, principal_id: UUID, kind: str, document_version: str
    ) -> LegalAcceptance | None: ...


class ContactRepository(Protocol):
    def save(self, contact: Contact) -> None: ...

    def get(self, contact_id: UUID) -> Contact | None: ...

    def list_for_owner(self, owner_principal_id: UUID) -> tuple[Contact, ...]: ...


class AddressRepository(Protocol):
    def save(self, address: Address) -> None: ...

    def get(self, address_id: UUID) -> Address | None: ...

    def list_for_owner(
        self, owner_principal_id: UUID, kind: AddressKind | None = None
    ) -> tuple[Address, ...]: ...

    def find_default(
        self, owner_principal_id: UUID, kind: AddressKind
    ) -> Address | None: ...


class CustomerUnitOfWork(Protocol):
    @property
    def profiles(self) -> ProfileRepository: ...

    @property
    def legal_acceptances(self) -> LegalAcceptanceRepository: ...

    @property
    def contacts(self) -> ContactRepository: ...

    @property
    def addresses(self) -> AddressRepository: ...

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
