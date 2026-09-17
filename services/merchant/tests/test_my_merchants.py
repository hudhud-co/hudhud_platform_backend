"""The signed-in principal's own merchants.

Customer App v3 `home` → `yourStores` lists "Personal account", every store the person
owns, and every store they work at. Before this endpoint neither half was reachable:

* `GET /merchant/principals/{id}/store-access` returns **team memberships only** — an
  owner is not a member of their own merchant, so an owner saw an empty list;
* `ApplicationResponse` carries no `merchant_id`, so even an approved application did not
  reveal which merchant it created.

So the app had no way to enumerate the stores it may act for. The principal is taken from
the authenticated session; a client-supplied id is never identity.
"""

from __future__ import annotations

from merchant_fixtures import APPLICANT, approved_merchant
from test_merchant_api import (
    MEMBER_TOKEN,
    OUTSIDER_TOKEN,
    OWNER_TOKEN,
    auth,
    build_client,
)

PATH = "/merchant/me/merchants"

#: The fake authorizer resolves this number to the MEMBER principal on accept.
MEMBER_PHONE = "+9647701234567"


def test_listing_my_merchants_needs_a_token() -> None:
    client, _ = build_client()
    assert client.get(PATH).status_code == 401


def test_a_plain_customer_owns_nothing() -> None:
    client, _ = build_client()
    response = client.get(PATH, headers=auth(OWNER_TOKEN))
    assert response.status_code == 200
    assert response.json() == []


def test_an_owner_sees_the_merchant_their_application_created() -> None:
    """Regression: store-access returned [] for an owner, hiding their own store."""
    client, uow = build_client()
    merchant = approved_merchant(uow, applicant=APPLICANT)

    response = client.get(PATH, headers=auth(OWNER_TOKEN))
    assert response.status_code == 200
    rows = response.json()
    assert [row["merchant_id"] for row in rows] == [str(merchant.merchant_id)]
    assert rows[0]["relationship"] == "OWNER"
    assert rows[0]["display_name"] == merchant.display_name
    assert rows[0]["merchant_code"] == merchant.merchant_code


def test_owning_several_stores_lists_them_all() -> None:
    """v3 `addStore`: "Register another business on this account"."""
    client, uow = build_client()
    first = approved_merchant(uow, applicant=APPLICANT)
    second = approved_merchant(uow, applicant=APPLICANT)

    rows = client.get(PATH, headers=auth(OWNER_TOKEN)).json()
    assert {row["merchant_id"] for row in rows} == {
        str(first.merchant_id),
        str(second.merchant_id),
    }
    assert {row["relationship"] for row in rows} == {"OWNER"}


def test_another_principals_store_is_not_listed() -> None:
    client, uow = build_client()
    approved_merchant(uow, applicant=APPLICANT)

    response = client.get(PATH, headers=auth(OUTSIDER_TOKEN))
    assert response.status_code == 200
    assert response.json() == []


def test_identity_comes_from_the_session_not_the_query() -> None:
    client, uow = build_client()
    approved_merchant(uow, applicant=APPLICANT)

    response = client.get(
        PATH, params={"principal_id": str(APPLICANT)}, headers=auth(OUTSIDER_TOKEN)
    )
    assert response.status_code == 200
    assert response.json() == [], "a named principal must not widen the answer"


def _invite_member(client, merchant_id, store_id) -> str:
    """Invite by phone — a membership is created against a number, not a principal."""
    invite = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": MEMBER_PHONE, "store_ids": [str(store_id)]},
        headers=auth(OWNER_TOKEN),
    )
    assert invite.status_code == 201, invite.text
    return invite.json()["membership_id"]


def _a_store(client, merchant_id) -> str:
    created = client.post(
        f"/merchant/merchants/{merchant_id}/stores",
        json={"name": "Karbala branch", "governorate": "KARBALA",
              "address_line": "House 14, near the bakery"},
        headers=auth(OWNER_TOKEN),
    )
    assert created.status_code == 201, created.text
    return created.json()["store_id"]


def test_a_member_sees_the_store_they_work_at() -> None:
    """The workplace half of `yourStores`, with the capabilities the UI must gate on."""
    client, uow = build_client()
    merchant = approved_merchant(uow, applicant=APPLICANT)
    store_id = _a_store(client, merchant.merchant_id)
    membership_id = _invite_member(client, merchant.merchant_id, store_id)

    accepted = client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )
    assert accepted.status_code == 200, accepted.text

    rows = client.get(PATH, headers=auth(MEMBER_TOKEN)).json()
    assert [row["merchant_id"] for row in rows] == [str(merchant.merchant_id)]
    assert rows[0]["relationship"] == "MEMBER"
    assert rows[0]["role"] == "WAREHOUSE_KEEPER"
    assert "store_parcel:read" in rows[0]["permissions"]
    # The keeper denylist must be visible to the client so the UI gates correctly.
    assert "shipment:create" not in rows[0]["permissions"]
    assert "wallet:read" not in rows[0]["permissions"]


def test_a_pending_invitation_grants_nothing() -> None:
    """v3 `teamSent`: "until then the member stays pending and sees nothing"."""
    client, uow = build_client()
    merchant = approved_merchant(uow, applicant=APPLICANT)
    store_id = _a_store(client, merchant.merchant_id)
    _invite_member(client, merchant.merchant_id, store_id)

    assert client.get(PATH, headers=auth(MEMBER_TOKEN)).json() == []


def test_an_owner_row_carries_no_team_permissions() -> None:
    """An owner's reach comes from ownership, not from a membership permission set."""
    client, uow = build_client()
    approved_merchant(uow, applicant=APPLICANT)

    row = client.get(PATH, headers=auth(OWNER_TOKEN)).json()[0]
    assert row["role"] is None
    assert row["permissions"] == []
