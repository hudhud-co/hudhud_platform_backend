"""In-memory Customer unit of work for unit tests."""

from __future__ import annotations

import copy
from contextvars import ContextVar
from uuid import UUID

from customer.domain.entities import Address, Contact, CustomerProfile, LegalAcceptance
from customer.domain.value_objects import AddressKind

_COLLECTIONS = ("profiles", "legal", "contacts", "addresses")


class InMemoryCustomerUnitOfWork:
    def __init__(self) -> None:
        self._committed: dict[str, dict] = {name: {} for name in _COLLECTIONS}
        self._tx_var: ContextVar[dict[str, dict] | None] = ContextVar(
            "customer_memory_tx", default=None
        )
        self._profiles = _ProfileRepo(self)
        self._legal = _LegalRepo(self)
        self._contacts = _ContactRepo(self)
        self._addresses = _AddressRepo(self)

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
    def profiles(self) -> _ProfileRepo:
        return self._profiles

    @property
    def legal_acceptances(self) -> _LegalRepo:
        return self._legal

    @property
    def contacts(self) -> _ContactRepo:
        return self._contacts

    @property
    def addresses(self) -> _AddressRepo:
        return self._addresses


class _ProfileRepo:
    def __init__(self, store: InMemoryCustomerUnitOfWork) -> None:
        self._store = store

    def save(self, profile: CustomerProfile) -> None:
        self._store._working("profiles")[profile.principal_id] = copy.deepcopy(profile)

    def get(self, principal_id: UUID) -> CustomerProfile | None:
        found = self._store._working("profiles").get(principal_id)
        return copy.deepcopy(found) if found is not None else None


class _LegalRepo:
    def __init__(self, store: InMemoryCustomerUnitOfWork) -> None:
        self._store = store

    def save(self, acceptance: LegalAcceptance) -> None:
        self._store._working("legal")[acceptance.acceptance_id] = copy.deepcopy(acceptance)

    def list_for_principal(self, principal_id: UUID) -> tuple[LegalAcceptance, ...]:
        return tuple(
            copy.deepcopy(a)
            for a in self._store._working("legal").values()
            if a.principal_id == principal_id
        )

    def find(
        self, principal_id: UUID, kind: str, document_version: str
    ) -> LegalAcceptance | None:
        for acceptance in self._store._working("legal").values():
            if (
                acceptance.principal_id == principal_id
                and acceptance.kind.value == kind
                and acceptance.document_version == document_version
            ):
                return copy.deepcopy(acceptance)
        return None


class _ContactRepo:
    def __init__(self, store: InMemoryCustomerUnitOfWork) -> None:
        self._store = store

    def save(self, contact: Contact) -> None:
        self._store._working("contacts")[contact.contact_id] = copy.deepcopy(contact)

    def get(self, contact_id: UUID) -> Contact | None:
        found = self._store._working("contacts").get(contact_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_owner(self, owner_principal_id: UUID) -> tuple[Contact, ...]:
        return tuple(
            copy.deepcopy(c)
            for c in self._store._working("contacts").values()
            if c.owner_principal_id == owner_principal_id
        )


class _AddressRepo:
    def __init__(self, store: InMemoryCustomerUnitOfWork) -> None:
        self._store = store

    def save(self, address: Address) -> None:
        self._store._working("addresses")[address.address_id] = copy.deepcopy(address)

    def get(self, address_id: UUID) -> Address | None:
        found = self._store._working("addresses").get(address_id)
        return copy.deepcopy(found) if found is not None else None

    def list_for_owner(
        self, owner_principal_id: UUID, kind: AddressKind | None = None
    ) -> tuple[Address, ...]:
        return tuple(
            copy.deepcopy(a)
            for a in self._store._working("addresses").values()
            if a.owner_principal_id == owner_principal_id
            and (kind is None or a.kind is kind)
        )

    def find_default(
        self, owner_principal_id: UUID, kind: AddressKind
    ) -> Address | None:
        for address in self._store._working("addresses").values():
            if (
                address.owner_principal_id == owner_principal_id
                and address.kind is kind
                and address.is_default
                and address.archived_at is None
            ):
                return copy.deepcopy(address)
        return None
