"""Store team membership and the permission ceiling (MER-20, SEC-05)."""

from __future__ import annotations

import pytest
from merchant_fixtures import (
    MEMBER,
    OUTSIDER,
    a_store,
    an_invite,
    approved_merchant,
    build_store,
    new_id,
    store_service,
    team_service,
)

from merchant.application.team_service import (
    InviteDraft,
    TeamService,
    normalize_phone,
)
from merchant.domain.errors import (
    ForbiddenTeamCapability,
    InvalidPhoneNumber,
    MembershipAlreadyExists,
    MembershipNotPending,
    MembershipStoresRequired,
    StoreArchived,
    StoreNotFound,
)
from merchant.domain.value_objects import (
    FORBIDDEN_TEAM_CAPABILITIES,
    MembershipStatus,
    StorePermission,
    TeamRole,
)

# ------------------------------------------------------------------ invite


def test_an_invitation_starts_pending_and_grants_nothing() -> None:
    """"Until then the member stays pending and sees nothing of your store." """
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)

    membership = team_service(uow).invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    assert membership.status is MembershipStatus.PENDING
    assert membership.member_principal_id is None
    assert membership.permissions == frozenset()


def test_a_pending_membership_contributes_no_store_access() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    team_service(uow).invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    assert team_service(uow).store_access(principal_id=MEMBER) == ()


def test_accepting_is_what_creates_permission() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    accepted = service.accept(
        membership_id=membership.membership_id, member_principal_id=MEMBER
    )

    assert accepted.status is MembershipStatus.ACTIVE
    assert accepted.member_principal_id == MEMBER
    assert accepted.permissions == {
        StorePermission.STORE_PARCEL_READ,
        StorePermission.STORE_PARCEL_PREPARE,
        StorePermission.COURIER_HANDOVER_COMPLETE,
    }


def test_an_invitation_must_name_at_least_one_branch() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    a_store(uow, merchant)

    with pytest.raises(MembershipStoresRequired):
        team_service(uow).invite(
            merchant_id=merchant.merchant_id,
            draft=InviteDraft(phone="+9647709998888", store_ids=()),
        )


def test_a_branch_from_another_merchant_cannot_be_assigned() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    a_store(uow, merchant)

    with pytest.raises(StoreNotFound):
        team_service(uow).invite(
            merchant_id=merchant.merchant_id, draft=an_invite((new_id(),))
        )


def test_an_archived_branch_cannot_be_assigned() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")
    store_service(uow).archive_store(store_id=second.store_id)

    with pytest.raises(StoreArchived):
        team_service(uow).invite(
            merchant_id=merchant.merchant_id, draft=an_invite((second.store_id,))
        )


def test_one_live_membership_per_number_per_merchant() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    with pytest.raises(MembershipAlreadyExists):
        service.invite(
            merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
        )


def test_a_removed_member_can_be_invited_again() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.remove(membership_id=membership.membership_id)

    again = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    assert again.membership_id != membership.membership_id


# ------------------------------------------------------------------ response


def test_declining_ends_the_invitation_without_access() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )

    declined = service.decline(
        membership_id=membership.membership_id, member_principal_id=MEMBER
    )

    assert declined.status is MembershipStatus.DECLINED
    assert declined.permissions == frozenset()
    assert service.store_access(principal_id=MEMBER) == ()


def test_an_accepted_membership_cannot_be_accepted_again() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)

    with pytest.raises(MembershipNotPending):
        service.accept(
            membership_id=membership.membership_id, member_principal_id=OUTSIDER
        )


def test_removing_a_member_revokes_access_immediately() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)

    service.remove(membership_id=membership.membership_id)

    assert service.store_access(principal_id=MEMBER) == ()


# ------------------------------------------------------------------ scope


def test_a_member_sees_only_the_branches_they_are_assigned_to() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    assigned = a_store(uow, merchant, name="Center")
    other = a_store(uow, merchant, name="Old City")
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((assigned.store_id,))
    )
    accepted = service.accept(
        membership_id=membership.membership_id, member_principal_id=MEMBER
    )

    assert accepted.covers_store(assigned.store_id) is True
    assert accepted.covers_store(other.store_id) is False


def test_store_access_reports_role_permissions_and_branches() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((store.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)

    access = service.store_access(principal_id=MEMBER)

    assert len(access) == 1
    assert access[0].merchant_id == merchant.merchant_id
    assert access[0].role is TeamRole.WAREHOUSE_KEEPER
    assert access[0].store_ids == (store.store_id,)


def test_branches_can_be_changed_after_acceptance() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    first = a_store(uow, merchant, name="Center")
    second = a_store(uow, merchant, name="Old City")
    service = team_service(uow)
    membership = service.invite(
        merchant_id=merchant.merchant_id, draft=an_invite((first.store_id,))
    )
    service.accept(membership_id=membership.membership_id, member_principal_id=MEMBER)

    updated = service.update_branches(
        membership_id=membership.membership_id,
        store_ids=(first.store_id, second.store_id),
    )

    assert set(updated.store_ids) == {first.store_id, second.store_id}


def test_duplicate_branches_are_collapsed() -> None:
    uow = build_store()
    merchant = approved_merchant(uow)
    store = a_store(uow, merchant)

    membership = team_service(uow).invite(
        merchant_id=merchant.merchant_id,
        draft=an_invite((store.store_id, store.store_id)),
    )

    assert membership.store_ids == (store.store_id,)


# ------------------------------------------------------------------ ceiling


@pytest.mark.parametrize("capability", sorted(FORBIDDEN_TEAM_CAPABILITIES))
def test_no_team_member_may_ever_hold_a_forbidden_capability(capability: str) -> None:
    """"Cannot create, edit or cancel a shipment, and never sees the wallet or COD." """
    with pytest.raises(ForbiddenTeamCapability):
        TeamService.assert_capability_permitted(capability)


def test_the_only_role_is_warehouse_keeper() -> None:
    """"More roles are coming. For now every member is a warehouse keeper." """
    assert [role.value for role in TeamRole] == ["WAREHOUSE_KEEPER"]


def test_no_role_grants_anything_outside_the_declared_permission_set() -> None:
    granted = {
        permission.value
        for permission in TeamService.permissions_for(TeamRole.WAREHOUSE_KEEPER)
    }
    assert granted & FORBIDDEN_TEAM_CAPABILITIES == set()


# ------------------------------------------------------------------ phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+9647701234567", "+9647701234567"),
        ("009647701234567", "+9647701234567"),
        ("07701234567", "+9647701234567"),
        ("+964 770 123 4567", "+9647701234567"),
    ],
)
def test_phone_numbers_normalize_to_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "+1", "12345"])
def test_an_unusable_phone_number_is_refused(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(raw)
