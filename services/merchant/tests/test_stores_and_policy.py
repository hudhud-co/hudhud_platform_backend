"""Stores and branches (MER-16) and the standing shipment policy (MER-10/14/15)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from merchant_fixtures import (
    a_store,
    approved_merchant,
    build_store,
    new_id,
    store_service,
)

from merchant.application.store_service import (
    PLATFORM_FIXED_POLICY,
    PLATFORM_FIXED_POLICY_KEYS,
    StoreDraft,
)
from merchant.domain.errors import (
    LastStoreCannotBeArchived,
    MerchantNotActive,
    MerchantNotFound,
    SenderMayNotSetPlatformPolicy,
    StoreArchived,
    UnknownGovernorate,
)
from merchant.domain.value_objects import DeliveryFeePayer, GeoPoint, MerchantStatus

# ------------------------------------------------------------------ stores


def test_the_first_store_becomes_the_default_pickup_point() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    store = a_store(uow, merchant)

    assert store.is_default_pickup is True


def test_a_second_store_does_not_steal_the_default() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    first = a_store(uow, merchant, name="Center")

    second = a_store(uow, merchant, name="Old City")

    assert second.is_default_pickup is False
    assert uow.stores.find_default_pickup(merchant.merchant_id).store_id == first.store_id


def test_only_one_branch_can_be_the_default_pickup_point() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    first = a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")

    store_service(uow).set_default_pickup(store_id=second.store_id)

    defaults = [
        store
        for store in uow.stores.list_for_merchant(merchant.merchant_id)
        if store.is_default_pickup
    ]
    assert [store.store_id for store in defaults] == [second.store_id]
    assert uow.stores.get(first.store_id).is_default_pickup is False


def test_a_store_records_area_landmark_and_a_map_pin() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    store = store_service(uow).create_store(
        merchant_id=merchant.merchant_id,
        draft=StoreDraft(
            name="Center",
            governorate="karbala",
            address_line="Shop 8, Al-Nuqabat street",
            area="Center",
            landmark="opposite the university gate",
            geo=GeoPoint(latitude=Decimal("32.6160"), longitude=Decimal("44.0249")),
        ),
    )

    assert store.governorate == "KARBALA"
    assert store.area == "Center"
    assert store.landmark == "opposite the university gate"
    assert store.geo is not None


def test_an_unknown_governorate_is_refused() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(UnknownGovernorate):
        store_service(uow).create_store(
            merchant_id=merchant.merchant_id,
            draft=StoreDraft(
                name="Center", governorate="ATLANTIS", address_line="Somewhere"
            ),
        )


def test_the_only_store_cannot_be_archived() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)

    with pytest.raises(LastStoreCannotBeArchived):
        store_service(uow).archive_store(store_id=store.store_id)


def test_archiving_the_default_promotes_another_branch() -> None:
    """A merchant with branches but no pickup point could not book a pickup at all."""
    uow = build_store()
    merchant = approved_merchant(uow)
    first = a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")

    store_service(uow).archive_store(store_id=first.store_id)

    assert uow.stores.get(first.store_id).is_active is False
    assert uow.stores.get(second.store_id).is_default_pickup is True


def test_an_archived_store_cannot_be_edited() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")
    store_service(uow).archive_store(store_id=second.store_id)

    with pytest.raises(StoreArchived):
        store_service(uow).update_store(store_id=second.store_id, name="Renamed")


def test_archived_stores_are_hidden_unless_asked_for() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")
    store_service(uow).archive_store(store_id=second.store_id)

    active = store_service(uow).list_stores(merchant_id=merchant.merchant_id)
    everything = store_service(uow).list_stores(
        merchant_id=merchant.merchant_id, include_archived=True
    )

    assert len(active) == 1
    assert len(everything) == 2


def test_a_store_cannot_be_created_for_an_unknown_merchant() -> None:
    uow = build_store()

    with pytest.raises(MerchantNotFound):
        store_service(uow).create_store(
            merchant_id=new_id(),
            draft=StoreDraft(name="Center", governorate="KARBALA", address_line="x"),
        )


def test_a_suspended_merchant_cannot_open_a_store() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    merchant.status = MerchantStatus.SUSPENDED
    uow.begin()
    uow.merchants.save(merchant)
    uow.commit()

    with pytest.raises(MerchantNotActive):
        store_service(uow).create_store(
            merchant_id=merchant.merchant_id,
            draft=StoreDraft(name="Center", governorate="KARBALA", address_line="x"),
        )


# ------------------------------------------------------------------ policy


def test_the_standing_policy_starts_with_every_add_on_off() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    policy = store_service(uow).get_policy(merchant_id=merchant.merchant_id)

    assert policy.open_box_allowed is False
    assert policy.photo_documentation_enabled is False
    assert policy.hudhud_packaging_enabled is False
    assert policy.delivery_fee_payer is DeliveryFeePayer.SENDER


def test_a_merchant_may_set_what_v63_says_a_sender_sets() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)

    policy = store_service(uow).update_policy(
        merchant_id=merchant.merchant_id,
        open_box_allowed=True,
        photo_documentation_enabled=True,
        hudhud_packaging_enabled=True,
        delivery_fee_payer=DeliveryFeePayer.RECEIVER,
    )

    assert policy.open_box_allowed is True
    assert policy.photo_documentation_enabled is True
    assert policy.hudhud_packaging_enabled is True
    assert policy.delivery_fee_payer is DeliveryFeePayer.RECEIVER


@pytest.mark.parametrize("key", sorted(PLATFORM_FIXED_POLICY_KEYS))
def test_a_sender_may_not_set_a_company_wide_rule(key: str) -> None:
    """v6.3 p.13 Role boundary — Sender (MER-15)."""
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(SenderMayNotSetPlatformPolicy) as caught:
        store_service(uow).update_policy(
            merchant_id=merchant.merchant_id, rejected_keys=(key,)
        )

    assert key in caught.value.keys


def test_a_rejected_key_blocks_the_whole_update() -> None:
    """A request that mixes a legal change with an illegal one changes nothing."""
    uow = build_store()
    merchant = approved_merchant(uow)

    with pytest.raises(SenderMayNotSetPlatformPolicy):
        store_service(uow).update_policy(
            merchant_id=merchant.merchant_id,
            open_box_allowed=True,
            rejected_keys=("hold_period_days",),
        )

    assert (
        store_service(uow).get_policy(merchant_id=merchant.merchant_id).open_box_allowed
        is False
    )


def test_the_fixed_policy_view_matches_the_v63_confirmed_decisions() -> None:
    assert PLATFORM_FIXED_POLICY["return_fee_owner"] == "MERCHANT"
    assert PLATFORM_FIXED_POLICY["hold_period_days"] == 3
    assert PLATFORM_FIXED_POLICY["door_wait_minutes"] == 10
    assert PLATFORM_FIXED_POLICY["liability_position"] == "HUDHUD_FULL_CUSTODY_LIABILITY"


def test_every_fixed_rule_is_visible_and_none_is_settable() -> None:
    assert set(PLATFORM_FIXED_POLICY) <= PLATFORM_FIXED_POLICY_KEYS
