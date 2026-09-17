"""Contacts and addresses: mandatory minimum, defaults, archive and restore.

Evidence: v6.3 p.12 (receiver phone and governorate mandatory; name, address and map pin
optional) and Customer App v3 ``addressBook`` / ``addContact`` / ``contactAddr`` /
``addPickup`` / ``mapPicker``.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from customer_fixtures import (
    BASE_TIME,
    OTHER_PRINCIPAL,
    PRINCIPAL,
    address_service,
    build_store,
    minutes,
)

from customer.application.address_book_service import (
    CreateAddressCommand,
    CreateContactCommand,
)
from customer.domain.errors import (
    AddressArchived,
    AddressNotFound,
    AddressNotOwnedByCustomer,
    ContactNotFound,
    LastAddressCannotBeArchived,
    ReceiverContactIncomplete,
    UnknownGovernorate,
)
from customer.domain.value_objects import AddressKind, GeoPoint


def _contact(service, *, owner=PRINCIPAL, phone="+9647701820934", gov="BAGHDAD", name=None):
    return service.create_contact(
        CreateContactCommand(
            owner_principal_id=owner,
            phone=phone,
            governorate=gov,
            occurred_at=BASE_TIME,
            display_name=name,
        )
    )


def _address(service, *, owner=PRINCIPAL, kind=AddressKind.DELIVERY, gov="BAGHDAD",
             line="Al-Jadriya, house 14", default=False, contact_id=None, geo=None):
    return service.create_address(
        CreateAddressCommand(
            owner_principal_id=owner,
            kind=kind,
            governorate=gov,
            line=line,
            occurred_at=BASE_TIME,
            make_default=default,
            contact_id=contact_id,
            geo=geo,
        )
    )


# ---------------------------------------------------------------- contacts


def test_a_phone_and_a_governorate_are_enough_to_save_a_receiver():
    """Everything else is optional — v6.3 p.12 makes name and address optional."""
    store = build_store()

    contact = _contact(address_service(store))

    assert contact.display_name is None
    assert contact.governorate == "BAGHDAD"
    assert contact.is_active is True


def test_a_receiver_without_a_phone_is_refused():
    store = build_store()

    with pytest.raises(ReceiverContactIncomplete) as excinfo:
        _contact(address_service(store), phone="  ")

    assert "phone" in excinfo.value.missing


def test_a_receiver_without_a_governorate_is_refused():
    store = build_store()

    with pytest.raises(ReceiverContactIncomplete):
        _contact(address_service(store), gov="")


def test_an_unknown_governorate_is_refused_rather_than_stored():
    store = build_store()

    with pytest.raises(UnknownGovernorate):
        _contact(address_service(store), gov="ATLANTIS")


def test_a_governorate_is_normalised_so_spelling_does_not_fragment_routing():
    store = build_store()

    contact = _contact(address_service(store), gov="salah al din")

    assert contact.governorate == "SALAH_AL_DIN"


def test_archiving_a_contact_keeps_it_out_of_the_active_list():
    store = build_store()
    service = address_service(store)
    contact = _contact(service)

    service.archive_contact(
        contact_id=contact.contact_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(1),
    )

    assert service.list_contacts(PRINCIPAL) == ()
    assert len(service.list_contacts(PRINCIPAL, include_archived=True)) == 1


def test_a_customer_cannot_archive_someone_elses_contact():
    store = build_store()
    service = address_service(store)
    contact = _contact(service)

    with pytest.raises(ContactNotFound):
        service.archive_contact(
            contact_id=contact.contact_id,
            owner_principal_id=OTHER_PRINCIPAL,
            occurred_at=BASE_TIME,
        )


# --------------------------------------------------------------- addresses


def test_the_first_address_of_a_kind_becomes_the_default_automatically():
    """Otherwise a customer with exactly one pickup address would have no default."""
    store = build_store()

    address = _address(address_service(store), kind=AddressKind.PICKUP)

    assert address.is_default is True


def test_making_a_second_address_default_demotes_the_first():
    store = build_store()
    service = address_service(store)
    first = _address(service)

    second = _address(service, line="Al-Mansour, house 2", default=True)

    assert second.is_default is True
    assert service.list_addresses(PRINCIPAL)[0].address_id == first.address_id
    stored_first = [
        a for a in service.list_addresses(PRINCIPAL) if a.address_id == first.address_id
    ][0]
    assert stored_first.is_default is False


def test_delivery_and_pickup_defaults_are_independent():
    """A receiver's home must never become a merchant pickup point."""
    store = build_store()
    service = address_service(store)

    delivery = _address(service, kind=AddressKind.DELIVERY)
    pickup = _address(service, kind=AddressKind.PICKUP, line="Store, Karrada")

    assert delivery.is_default is True
    assert pickup.is_default is True


def test_a_map_pin_is_optional_but_validated_when_supplied():
    store = build_store()
    service = address_service(store)

    pinned = _address(
        service, geo=GeoPoint(latitude=Decimal("33.31"), longitude=Decimal("44.36"))
    )

    assert pinned.geo is not None
    with pytest.raises(ValueError, match="latitude"):
        GeoPoint(latitude=Decimal("120"), longitude=Decimal("44.36"))


def test_an_address_may_reference_a_saved_contact():
    store = build_store()
    service = address_service(store)
    contact = _contact(service)

    address = _address(service, contact_id=contact.contact_id)

    assert address.contact_id == contact.contact_id


def test_an_address_cannot_reference_someone_elses_contact():
    store = build_store()
    service = address_service(store)
    contact = _contact(service, owner=OTHER_PRINCIPAL)

    with pytest.raises(ContactNotFound):
        _address(service, contact_id=contact.contact_id)


def test_an_address_without_a_line_is_refused():
    store = build_store()

    with pytest.raises(ReceiverContactIncomplete):
        _address(address_service(store), line="   ")


def test_archiving_hides_an_address_without_deleting_it():
    """Shipments already reference it, so the row must survive."""
    store = build_store()
    service = address_service(store)
    _address(service)
    second = _address(service, line="Al-Mansour")

    service.archive_address(
        address_id=second.address_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(1),
    )

    assert len(service.list_addresses(PRINCIPAL)) == 1
    assert len(service.list_addresses(PRINCIPAL, include_archived=True)) == 2


def test_archiving_the_default_promotes_another_address():
    store = build_store()
    service = address_service(store)
    default = _address(service)
    _address(service, line="Al-Mansour")

    service.archive_address(
        address_id=default.address_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(1),
    )

    remaining = service.list_addresses(PRINCIPAL)
    assert len(remaining) == 1
    assert remaining[0].is_default is True


def test_the_only_address_cannot_be_archived():
    """Archiving it would leave the customer unable to send anything."""
    store = build_store()
    service = address_service(store)
    only = _address(service)

    with pytest.raises(LastAddressCannotBeArchived):
        service.archive_address(
            address_id=only.address_id,
            owner_principal_id=PRINCIPAL,
            occurred_at=BASE_TIME + minutes(1),
        )


def test_a_restored_address_does_not_steal_the_default_back():
    store = build_store()
    service = address_service(store)
    first = _address(service)
    _address(service, line="Al-Mansour")
    service.archive_address(
        address_id=first.address_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(1),
    )

    restored = service.restore_address(
        address_id=first.address_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(2),
    )

    assert restored.is_active is True
    assert restored.is_default is False


def test_setting_an_archived_address_as_default_is_refused():
    store = build_store()
    service = address_service(store)
    _address(service)
    second = _address(service, line="Al-Mansour")
    service.archive_address(
        address_id=second.address_id,
        owner_principal_id=PRINCIPAL,
        occurred_at=BASE_TIME + minutes(1),
    )

    with pytest.raises(AddressArchived):
        service.set_default(
            address_id=second.address_id,
            owner_principal_id=PRINCIPAL,
            occurred_at=BASE_TIME + minutes(2),
        )


def test_a_customer_cannot_touch_another_customers_address():
    store = build_store()
    service = address_service(store)
    address = _address(service)

    with pytest.raises(AddressNotOwnedByCustomer):
        service.set_default(
            address_id=address.address_id,
            owner_principal_id=OTHER_PRINCIPAL,
            occurred_at=BASE_TIME,
        )


def test_an_unknown_address_is_not_found():
    store = build_store()

    with pytest.raises(AddressNotFound):
        address_service(store).set_default(
            address_id=uuid4(),
            owner_principal_id=PRINCIPAL,
            occurred_at=BASE_TIME,
        )


def test_addresses_can_be_listed_by_kind():
    store = build_store()
    service = address_service(store)
    _address(service, kind=AddressKind.DELIVERY)
    _address(service, kind=AddressKind.PICKUP, line="Store")

    assert len(service.list_addresses(PRINCIPAL, kind=AddressKind.PICKUP)) == 1
