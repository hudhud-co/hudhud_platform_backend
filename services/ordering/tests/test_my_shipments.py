"""The signed-in sender's own parcel list.

Customer App v3 states `parcels` (Outgoing/Incoming tabs) and the `home` active-parcel
summary need every shipment request the signed-in principal authored, across orders.
Before this endpoint the only reads were by `order_id` or by `request_id`, so a client
could not build that list without already knowing every order it had ever created.

The principal is taken from the authenticated session. A client-supplied principal or
merchant id is never trusted as identity: `?merchant_id=` only narrows a list the actor is
already entitled to, and is refused when the actor has no access to that merchant.
"""

from __future__ import annotations

from ordering_fixtures import (
    MERCHANT_ID,
    MERCHANT_OWNER,
    build_store,
    draft,
    new_id,
    send_service,
)

# Both modules define `customer_order`: the fixture builds one in a unit of work, the API
# helper creates one over HTTP. Alias so the direct-store test can use the former.
from ordering_fixtures import customer_order as customer_order_in_store
from test_ordering_api import (
    CUSTOMER_TOKEN,
    KEEPER_TOKEN,
    OPS_TOKEN,
    OWNER_TOKEN,
    a_draft,
    auth,
    build_client,
    customer_order,
    merchant_order,
)

from ordering.infrastructure.persistence.models import ShipmentRequestRow

PATH = "/ordering/me/shipments"


def _add_shipment(client, order_id: str, token: str, **draft) -> str:
    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(**draft),
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["request_id"]


# ------------------------------------------------------------------ auth


def test_listing_my_shipments_needs_a_token() -> None:
    client, _ = build_client()
    assert client.get(PATH).status_code == 401


def test_a_new_principal_has_an_empty_list() -> None:
    client, _ = build_client()
    response = client.get(PATH, headers=auth(CUSTOMER_TOKEN))
    assert response.status_code == 200
    assert response.json() == []


# ------------------------------------------------------------------ scoping


def test_a_customer_sees_only_their_own_shipments() -> None:
    client, _ = build_client()

    mine = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)
    theirs = _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    response = client.get(PATH, headers=auth(CUSTOMER_TOKEN))
    assert response.status_code == 200
    returned = {item["request_id"] for item in response.json()}
    assert returned == {mine}
    assert theirs not in returned


def test_the_list_spans_several_orders() -> None:
    client, _ = build_client()

    first = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)
    second = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)

    response = client.get(PATH, headers=auth(CUSTOMER_TOKEN))
    assert {item["request_id"] for item in response.json()} == {first, second}


def test_identity_comes_from_the_session_not_the_query() -> None:
    """A caller cannot read someone else's parcels by naming them."""
    client, _ = build_client()
    mine = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)
    _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    # Ask, as the customer, for the merchant owner's parcels. The parameter is not an
    # identity input, so the answer is still only the customer's own.
    response = client.get(
        PATH,
        params={"principal_id": str(MERCHANT_OWNER)},
        headers=auth(CUSTOMER_TOKEN),
    )
    assert response.status_code == 200
    assert {item["request_id"] for item in response.json()} == {mine}


# ------------------------------------------------------------------ merchant scope


def test_an_owner_can_narrow_to_a_merchant_they_act_for() -> None:
    client, _ = build_client()
    store_parcel = _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    response = client.get(
        PATH, params={"merchant_id": str(MERCHANT_ID)}, headers=auth(OWNER_TOKEN)
    )
    assert response.status_code == 200
    assert {item["request_id"] for item in response.json()} == {store_parcel}


def test_narrowing_to_a_merchant_you_cannot_act_for_is_refused() -> None:
    client, _ = build_client()
    response = client.get(
        PATH, params={"merchant_id": str(new_id())}, headers=auth(CUSTOMER_TOKEN)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "merchant_not_found"


def test_a_warehouse_keeper_may_read_the_store_parcels() -> None:
    """`store_parcel:read` is exactly the capability the v3 workplace views need."""
    client, _ = build_client()
    store_parcel = _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    response = client.get(
        PATH, params={"merchant_id": str(MERCHANT_ID)}, headers=auth(KEEPER_TOKEN)
    )
    assert response.status_code == 200
    assert {item["request_id"] for item in response.json()} == {store_parcel}


def test_a_keeper_without_a_merchant_filter_sees_nothing_of_the_store() -> None:
    """A keeper authors nothing, so their personal list stays empty."""
    client, _ = build_client()
    _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    response = client.get(PATH, headers=auth(KEEPER_TOKEN))
    assert response.status_code == 200
    assert response.json() == []


def test_operations_must_still_name_a_merchant() -> None:
    """Operations is not handed a cross-tenant dump by omitting the filter."""
    client, _ = build_client()
    _add_shipment(client, merchant_order(client), OWNER_TOKEN)

    unscoped = client.get(PATH, headers=auth(OPS_TOKEN))
    assert unscoped.status_code == 200
    assert unscoped.json() == []

    scoped = client.get(
        PATH, params={"merchant_id": str(MERCHANT_ID)}, headers=auth(OPS_TOKEN)
    )
    assert scoped.status_code == 200
    assert len(scoped.json()) == 1


# ------------------------------------------------------------------ shape


def test_the_rows_are_full_shipment_responses() -> None:
    client, _ = build_client()
    request_id = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)

    row = client.get(PATH, headers=auth(CUSTOMER_TOKEN)).json()[0]
    detail = client.get(
        f"/ordering/shipments/{request_id}", headers=auth(CUSTOMER_TOKEN)
    ).json()
    assert row == detail, "the list must not diverge from the detail contract"


def test_status_filter_narrows_the_list() -> None:
    """A personal parcel starts at AWAITING_DROPOFF — the hub drop-off path.

    The v3 `parcels` tabs filter on exactly this, so the filter is asserted against the
    status the service really assigns rather than an assumed DRAFT.
    """
    client, _ = build_client()
    request_id = _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)

    kept = client.get(
        PATH, params={"status": "AWAITING_DROPOFF"}, headers=auth(CUSTOMER_TOKEN)
    )
    assert {item["request_id"] for item in kept.json()} == {request_id}

    dropped = client.get(
        PATH, params={"status": "REGISTERED"}, headers=auth(CUSTOMER_TOKEN)
    )
    assert dropped.json() == []


def test_the_filter_is_case_insensitive() -> None:
    client, _ = build_client()
    _add_shipment(client, customer_order(client), CUSTOMER_TOKEN)
    response = client.get(
        PATH, params={"status": "awaiting_dropoff"}, headers=auth(CUSTOMER_TOKEN)
    )
    assert len(response.json()) == 1


def test_an_unknown_status_filter_is_a_validation_error() -> None:
    client, _ = build_client()
    response = client.get(
        PATH, params={"status": "TELEPORTED"}, headers=auth(CUSTOMER_TOKEN)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_status"


# ------------------------------------------------------------------ persistence


def test_the_sender_lookups_are_index_backed() -> None:
    """`/ordering/me/shipments` filters on these columns on every call.

    Asserting the indexes here means a later migration cannot quietly turn the parcel
    list into a sequential scan of every shipment request on the platform.
    """
    indexed = {
        tuple(column.name for column in index.columns)
        for index in ShipmentRequestRow.__table__.indexes
    }
    assert ("sender_principal_id",) in indexed
    assert ("sender_merchant_id",) in indexed


def test_the_store_filters_by_sender_not_by_order() -> None:
    """The repository method the endpoint depends on, exercised directly."""
    uow = build_store()
    service = send_service(uow)
    order = customer_order_in_store(uow)
    created = service.add_shipment(order_id=order.order_id, draft=draft())

    mine = service.list_for_principal(order.sender.principal_id)
    assert [item.request_id for item in mine] == [created.request_id]

    assert service.list_for_principal(new_id()) == ()
