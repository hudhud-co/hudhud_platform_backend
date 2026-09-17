"""Contacts and the address book.

Two product rules shape this module. First, the minimum HUDHUD needs to reach and route
to a receiver is a phone number and a governorate (v6.3 p.12) — a name, a street address
and a map pin are all genuinely optional and must not be demanded. Second, an address is
archived rather than deleted, because shipments already reference it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from customer.domain.entities import Address, Contact
from customer.domain.errors import (
    AddressArchived,
    AddressNotFound,
    AddressNotOwnedByCustomer,
    ContactNotFound,
    LastAddressCannotBeArchived,
    ReceiverContactIncomplete,
    UnknownGovernorate,
)
from customer.domain.value_objects import GOVERNORATES, AddressKind, GeoPoint
from customer.ports.repository import CustomerUnitOfWork


@dataclass(frozen=True, slots=True)
class CreateContactCommand:
    owner_principal_id: UUID
    phone: str
    governorate: str
    occurred_at: datetime
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class CreateAddressCommand:
    owner_principal_id: UUID
    kind: AddressKind | str
    governorate: str
    line: str
    occurred_at: datetime
    contact_id: UUID | None = None
    landmark: str | None = None
    geo: GeoPoint | None = None
    make_default: bool = False


class AddressBookService:
    def __init__(self, unit_of_work: CustomerUnitOfWork) -> None:
        self._uow = unit_of_work

    # ---------------------------------------------------------------- contacts

    def create_contact(self, command: CreateContactCommand) -> Contact:
        phone = (command.phone or "").strip()
        governorate = _normalize_governorate(command.governorate)
        missing = []
        if not phone:
            missing.append("phone")
        if not governorate:
            missing.append("governorate")
        if missing:
            raise ReceiverContactIncomplete(tuple(missing))
        _assert_known_governorate(governorate)

        contact = Contact(
            contact_id=uuid4(),
            owner_principal_id=command.owner_principal_id,
            phone=phone,
            governorate=governorate,
            display_name=(command.display_name or "").strip() or None,
            created_at=command.occurred_at,
        )
        self._uow.begin()
        try:
            self._uow.contacts.save(contact)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return contact

    def archive_contact(
        self, *, contact_id: UUID, owner_principal_id: UUID, occurred_at: datetime
    ) -> Contact:
        self._uow.begin()
        try:
            contact = self._uow.contacts.get(contact_id)
            if contact is None or contact.owner_principal_id != owner_principal_id:
                raise ContactNotFound(str(contact_id))
            if contact.archived_at is None:
                contact.archived_at = occurred_at
                contact.version += 1
                self._uow.contacts.save(contact)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return contact

    def list_contacts(
        self, owner_principal_id: UUID, *, include_archived: bool = False
    ) -> tuple[Contact, ...]:
        self._uow.begin()
        try:
            contacts = self._uow.contacts.list_for_owner(owner_principal_id)
        finally:
            self._uow.rollback()
        if include_archived:
            return contacts
        return tuple(c for c in contacts if c.is_active)

    # --------------------------------------------------------------- addresses

    def create_address(self, command: CreateAddressCommand) -> Address:
        kind = _coerce_kind(command.kind)
        governorate = _normalize_governorate(command.governorate)
        _assert_known_governorate(governorate)
        line = (command.line or "").strip()
        if not line:
            raise ReceiverContactIncomplete(("line",))

        self._uow.begin()
        try:
            if command.contact_id is not None:
                contact = self._uow.contacts.get(command.contact_id)
                if (
                    contact is None
                    or contact.owner_principal_id != command.owner_principal_id
                ):
                    raise ContactNotFound(str(command.contact_id))

            existing_default = self._uow.addresses.find_default(
                command.owner_principal_id, kind
            )
            # The first address of a kind is the default whether or not the caller asked:
            # otherwise a customer with exactly one pickup address would have none.
            make_default = command.make_default or existing_default is None
            if make_default and existing_default is not None:
                existing_default.is_default = False
                existing_default.version += 1
                self._uow.addresses.save(existing_default)

            address = Address(
                address_id=uuid4(),
                owner_principal_id=command.owner_principal_id,
                kind=kind,
                governorate=governorate,
                line=line,
                contact_id=command.contact_id,
                landmark=(command.landmark or "").strip() or None,
                geo=command.geo,
                is_default=make_default,
                created_at=command.occurred_at,
            )
            self._uow.addresses.save(address)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return address

    def set_default(
        self, *, address_id: UUID, owner_principal_id: UUID, occurred_at: datetime
    ) -> Address:
        self._uow.begin()
        try:
            address = self._load_owned(address_id, owner_principal_id)
            if address.archived_at is not None:
                raise AddressArchived(str(address_id))
            if address.is_default:
                self._uow.commit()
                return address
            current = self._uow.addresses.find_default(owner_principal_id, address.kind)
            if current is not None and current.address_id != address.address_id:
                current.is_default = False
                current.version += 1
                self._uow.addresses.save(current)
            address.is_default = True
            address.version += 1
            self._uow.addresses.save(address)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        _ = occurred_at
        return address

    def archive_address(
        self, *, address_id: UUID, owner_principal_id: UUID, occurred_at: datetime
    ) -> Address:
        self._uow.begin()
        try:
            address = self._load_owned(address_id, owner_principal_id)
            if address.archived_at is not None:
                self._uow.commit()
                return address
            if address.is_default:
                alternatives = [
                    a
                    for a in self._uow.addresses.list_for_owner(
                        owner_principal_id, address.kind
                    )
                    if a.is_active and a.address_id != address.address_id
                ]
                if not alternatives:
                    raise LastAddressCannotBeArchived()
                replacement = alternatives[0]
                replacement.is_default = True
                replacement.version += 1
                self._uow.addresses.save(replacement)
                address.is_default = False
            address.archived_at = occurred_at
            address.version += 1
            self._uow.addresses.save(address)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        return address

    def restore_address(
        self, *, address_id: UUID, owner_principal_id: UUID, occurred_at: datetime
    ) -> Address:
        self._uow.begin()
        try:
            address = self._load_owned(address_id, owner_principal_id)
            if address.archived_at is not None:
                address.archived_at = None
                address.version += 1
                # Restoring never steals the default from whatever took over.
                if (
                    self._uow.addresses.find_default(owner_principal_id, address.kind)
                    is None
                ):
                    address.is_default = True
                self._uow.addresses.save(address)
        except Exception:
            self._uow.rollback()
            raise
        self._uow.commit()
        _ = occurred_at
        return address

    def list_addresses(
        self,
        owner_principal_id: UUID,
        *,
        kind: AddressKind | None = None,
        include_archived: bool = False,
    ) -> tuple[Address, ...]:
        self._uow.begin()
        try:
            addresses = self._uow.addresses.list_for_owner(owner_principal_id, kind)
        finally:
            self._uow.rollback()
        if include_archived:
            return addresses
        return tuple(a for a in addresses if a.is_active)

    def _load_owned(self, address_id: UUID, owner_principal_id: UUID) -> Address:
        address = self._uow.addresses.get(address_id)
        if address is None:
            raise AddressNotFound(str(address_id))
        if address.owner_principal_id != owner_principal_id:
            raise AddressNotOwnedByCustomer(str(address_id))
        return address


def _coerce_kind(value: AddressKind | str) -> AddressKind:
    if isinstance(value, AddressKind):
        return value
    try:
        return AddressKind(str(value).strip().upper())
    except ValueError as exc:
        raise ReceiverContactIncomplete(("kind",)) from exc


def _normalize_governorate(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "_").replace("-", "_")


def _assert_known_governorate(value: str) -> None:
    if value not in GOVERNORATES:
        raise UnknownGovernorate(value)
