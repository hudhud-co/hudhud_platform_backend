"""Ordering HTTP adapter: authorization, ownership and the public tracking surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from ordering_fixtures import (
    CUSTOMER,
    MERCHANT_ID,
    MERCHANT_OWNER,
    SERVICEABLE,
    STORE_ID,
    new_id,
)

from ordering.config import (
    OrderingSettings,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
)
from ordering.infrastructure.authorizers.identity import FakeOrderingAuthorizer
from ordering.infrastructure.authorizers.merchant_access import FakeMerchantAccess
from ordering.infrastructure.memory import InMemoryOrderingUnitOfWork
from ordering.main import create_app
from ordering.ports.authorization import OrderingActor, OrderingRole, StoreAccess

OWNER_TOKEN = "owner-token"
CUSTOMER_TOKEN = "customer-token"
KEEPER_TOKEN = "keeper-token"
OPS_TOKEN = "ops-token"
SUPPORT_TOKEN = "support-token"
KEEPER = new_id()
OPS = new_id()
SUPPORT = new_id()


def build_client(**settings_kwargs) -> tuple[TestClient, InMemoryOrderingUnitOfWork]:
    uow = InMemoryOrderingUnitOfWork()
    authorizer = FakeOrderingAuthorizer(
        token_actors={
            OWNER_TOKEN: OrderingActor(
                principal_id=MERCHANT_OWNER,
                roles=frozenset({OrderingRole.MERCHANT_OWNER}),
                merchant_ids=frozenset({MERCHANT_ID}),
            ),
            CUSTOMER_TOKEN: OrderingActor(
                principal_id=CUSTOMER, roles=frozenset({OrderingRole.CUSTOMER})
            ),
            KEEPER_TOKEN: OrderingActor(
                principal_id=KEEPER, roles=frozenset({OrderingRole.MERCHANT_MEMBER})
            ),
            OPS_TOKEN: OrderingActor(
                principal_id=OPS, roles=frozenset({OrderingRole.OPERATIONS})
            ),
            SUPPORT_TOKEN: OrderingActor(
                principal_id=SUPPORT, roles=frozenset({OrderingRole.SUPPORT})
            ),
        }
    )
    # A warehouse keeper: reads and prepares parcels, never authors them.
    merchant_access = FakeMerchantAccess(
        access={
            KEEPER: (
                StoreAccess(
                    merchant_id=MERCHANT_ID,
                    role="WAREHOUSE_KEEPER",
                    permissions=frozenset(
                        {
                            "store_parcel:read",
                            "store_parcel:prepare",
                            "courier_handover:complete",
                        }
                    ),
                    store_ids=(STORE_ID,),
                ),
            )
        }
    )
    settings_kwargs.setdefault("serviceable_governorates", tuple(sorted(SERVICEABLE)))
    settings_kwargs.setdefault("require_prohibited_acknowledgement", False)
    app = create_app(
        OrderingSettings(environment=RuntimeEnvironment.TEST, **settings_kwargs),
        unit_of_work=uow,
        authorizer=authorizer,
        merchant_access=merchant_access,
    )
    return TestClient(app), uow


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def a_receiver(**kwargs) -> dict:
    return {"phone": "+9647701234567", "governorate": "BAGHDAD", **kwargs}


def a_draft(**kwargs) -> dict:
    return {
        "receiver": a_receiver(),
        "description": "Cotton bedsheet set, 4 pieces",
        **kwargs,
    }


def merchant_order(client: TestClient) -> str:
    response = client.post(
        "/ordering/orders",
        json={"merchant_id": str(MERCHANT_ID), "store_id": str(STORE_ID)},
        headers=auth(OWNER_TOKEN),
    )
    assert response.status_code == 201, response.text
    return response.json()["order_id"]


def customer_order(client: TestClient) -> str:
    response = client.post("/ordering/orders", json={}, headers=auth(CUSTOMER_TOKEN))
    assert response.status_code == 201, response.text
    return response.json()["order_id"]


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401() -> None:
    client, _ = build_client()
    assert client.post("/ordering/orders", json={}).status_code == 401


def test_the_default_composition_denies_everything() -> None:
    app = create_app(
        OrderingSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryOrderingUnitOfWork(),
    )
    client = TestClient(app)
    assert client.post("/ordering/orders", json={}, headers=auth(OWNER_TOKEN)).status_code == 401


def test_production_requires_identity_and_merchant() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            OrderingSettings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://x/y",
            )
        )
    message = str(caught.value)
    assert "ORDERING_IDENTITY_BASE_URL" in message
    assert "ORDERING_MERCHANT_BASE_URL" in message


def test_readiness_reports_unconfigured_serviceability() -> None:
    client, _ = build_client(serviceable_governorates=())
    body = client.get("/ready").json()
    assert body["checks"]["serviceability_configured"] is False


# ------------------------------------------------------------------ ownership


def test_a_warehouse_keeper_cannot_open_a_merchant_order() -> None:
    """Customer App v3: a keeper "cannot create, edit or cancel a shipment"."""
    client, _ = build_client()

    response = client.post(
        "/ordering/orders",
        json={"merchant_id": str(MERCHANT_ID)},
        headers=auth(KEEPER_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "team_member_may_not_author_shipments"


def test_a_stranger_cannot_open_an_order_for_a_merchant_they_do_not_own() -> None:
    client, _ = build_client()

    response = client.post(
        "/ordering/orders",
        json={"merchant_id": str(MERCHANT_ID)},
        headers=auth(CUSTOMER_TOKEN),
    )

    assert response.status_code == 403


def test_an_owner_can_open_a_merchant_order() -> None:
    client, _ = build_client()
    response = client.post(
        "/ordering/orders",
        json={"merchant_id": str(MERCHANT_ID)},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["sender_kind"] == "MERCHANT"


def test_a_customer_order_names_no_merchant() -> None:
    client, _ = build_client()
    body = client.post("/ordering/orders", json={}, headers=auth(CUSTOMER_TOKEN)).json()
    assert body["sender_kind"] == "CUSTOMER"


def test_another_principals_order_answers_404_not_403() -> None:
    """Confirming the id exists would leak that someone else has that order."""
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(),
        headers=auth(CUSTOMER_TOKEN),
    )

    assert response.status_code == 404


def test_another_principals_shipment_answers_404() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    response = client.get(
        f"/ordering/shipments/{request_id}", headers=auth(CUSTOMER_TOKEN)
    )

    assert response.status_code == 404


def test_operations_may_read_any_shipment() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    assert (
        client.get(f"/ordering/shipments/{request_id}", headers=auth(OPS_TOKEN)).status_code
        == 200
    )


# ------------------------------------------------------------------ send rules


def test_a_customer_shipment_is_refused_cod_over_http() -> None:
    client, _ = build_client()
    order_id = customer_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(
            payment_terms="CASH_ON_DELIVERY",
            cod_amount={"minor_units": 450000, "currency": "IQD"},
        ),
        headers=auth(CUSTOMER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cod_not_available_for_customers"


def test_a_description_is_required_over_http() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(description="   "),
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 422


def test_a_merchant_shipment_waits_for_a_label() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    body = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()

    assert body["status"] == "AWAITING_LABEL"
    assert body["requires_label"] is True


def test_a_customer_shipment_never_waits_for_a_label() -> None:
    client, _ = build_client()
    order_id = customer_order(client)

    body = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(),
        headers=auth(CUSTOMER_TOKEN),
    ).json()

    assert body["status"] == "AWAITING_DROPOFF"
    assert body["requires_label"] is False


def test_a_response_reveals_only_the_last_four_digits_of_the_receiver() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    )

    assert response.json()["receiver_phone_last4"] == "4567"
    assert "9647701234" not in response.text


def test_pickup_readiness_reports_the_missing_labels() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    client.post(f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN))
    client.post(f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN))

    body = client.get(
        f"/ordering/orders/{order_id}/pickup-readiness", headers=auth(OWNER_TOKEN)
    ).json()

    assert body == {
        "order_id": order_id,
        "labelled": 0,
        "total": 2,
        "pickup_available": False,
        "blocked_reason": body["blocked_reason"],
    }
    assert "once every parcel carries a label" in body["blocked_reason"]


def test_pickup_is_refused_until_every_parcel_is_labelled() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    first = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]
    client.post(f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN))
    client.post(
        f"/ordering/shipments/{first}/label",
        json={"label_code": "HH-000001"},
        headers=auth(OWNER_TOKEN),
    )

    response = client.post(
        f"/ordering/orders/{order_id}/pickup",
        json={"window_start": "2026-09-15T09:00:00Z", "window_end": "2026-09-15T13:00:00Z"},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["unlabelled"] == 1


def test_a_bulk_batch_creates_every_row() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments/bulk",
        json={"shipments": [a_draft() for _ in range(4)]},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 201
    assert len(response.json()) == 4


# ------------------------------------------------------------------ amendments


def test_setting_a_company_wide_rule_is_403_and_names_the_key() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    response = client.patch(
        f"/ordering/shipments/{request_id}",
        json={"description": "Changed", "hold_period_days": 7},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["keys"] == ["hold_period_days"]


def test_a_support_correction_needs_support_or_operations() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    denied = client.post(
        f"/ordering/shipments/{request_id}/support-correction",
        json={"receiver": a_receiver(name="Zahra")},
        headers=auth(OWNER_TOKEN),
    )
    allowed = client.post(
        f"/ordering/shipments/{request_id}/support-correction",
        json={"receiver": a_receiver(name="Zahra")},
        headers=auth(SUPPORT_TOKEN),
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_cancelling_before_pickup_works_over_http() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    response = client.post(
        f"/ordering/shipments/{request_id}/cancel",
        json={"reason": "SENDER_CHANGED_MIND"},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_an_invented_cancellation_reason_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    request_id = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["request_id"]

    response = client.post(
        f"/ordering/shipments/{request_id}/cancel",
        json={"reason": "BECAUSE"},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ pricing


def test_a_quote_without_a_published_rate_is_503_not_a_guess() -> None:
    client, _ = build_client()

    response = client.get(
        "/ordering/quote?origin_governorate=KARBALA&destination_governorate=BAGHDAD",
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "tariff_not_configured"


def test_a_merchant_cannot_publish_a_tariff_rate() -> None:
    client, _ = build_client()

    response = client.post(
        "/ordering/tariff-rates",
        json={
            "reference": "SELF-SERVE",
            "origin_governorate": "KARBALA",
            "destination_governorate": "BAGHDAD",
            "delivery_fee": {"minor_units": 1, "currency": "IQD"},
            "packaging_fee": {"minor_units": 0, "currency": "IQD"},
        },
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 403


def test_operations_publishes_a_rate_and_the_quote_uses_it() -> None:
    client, _ = build_client()
    client.post(
        "/ordering/tariff-rates",
        json={
            "reference": "TARIFF-2026-09",
            "origin_governorate": "KARBALA",
            "destination_governorate": "BAGHDAD",
            "delivery_fee": {"minor_units": 4250, "currency": "IQD"},
            "packaging_fee": {"minor_units": 1500, "currency": "IQD"},
        },
        headers=auth(OPS_TOKEN),
    )

    body = client.get(
        "/ordering/quote?origin_governorate=KARBALA&destination_governorate=BAGHDAD"
        "&hudhud_packaging=true",
        headers=auth(OWNER_TOKEN),
    ).json()

    assert body["delivery_fee"] == {"minor_units": 4250, "currency": "IQD"}
    assert body["total"] == {"minor_units": 5750, "currency": "IQD"}


def test_money_crosses_the_wire_as_integer_minor_units() -> None:
    """A JSON number with a fraction is a float somewhere down the line."""
    client, _ = build_client()
    order_id = merchant_order(client)

    body = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(
            payment_terms="CASH_ON_DELIVERY",
            cod_amount={"minor_units": 450000, "currency": "IQD"},
        ),
        headers=auth(OWNER_TOKEN),
    ).json()

    assert body["cod_amount"] == {"minor_units": 450000, "currency": "IQD"}
    assert isinstance(body["cod_amount"]["minor_units"], int)


def test_a_fractional_amount_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(
            payment_terms="CASH_ON_DELIVERY",
            cod_amount={"minor_units": 450000.5, "currency": "IQD"},
        ),
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 422


def test_an_unknown_currency_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)

    response = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(
            payment_terms="CASH_ON_DELIVERY",
            cod_amount={"minor_units": 1, "currency": "USD"},
        ),
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ public


def test_the_public_track_route_needs_no_token() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    code = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()["tracking_code"]

    response = client.get(f"/track/{code}")

    assert response.status_code == 200
    assert response.json()["tracking_code"] == code


def test_the_public_route_reveals_nothing_personal() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    code = client.post(
        f"/ordering/orders/{order_id}/shipments",
        json=a_draft(
            receiver=a_receiver(name="Zahra Hussein", address_line="House 14"),
            payment_terms="CASH_ON_DELIVERY",
            cod_amount={"minor_units": 450000, "currency": "IQD"},
        ),
        headers=auth(OWNER_TOKEN),
    ).json()["tracking_code"]

    body = client.get(f"/track/{code}").text

    assert "Zahra" not in body
    assert "House 14" not in body
    assert "450000" not in body
    assert "9647701234567" not in body


def test_an_unknown_code_and_a_cancelled_one_answer_alike() -> None:
    client, _ = build_client()
    order_id = merchant_order(client)
    created = client.post(
        f"/ordering/orders/{order_id}/shipments", json=a_draft(), headers=auth(OWNER_TOKEN)
    ).json()
    client.post(
        f"/ordering/shipments/{created['request_id']}/cancel",
        json={"reason": "SENDER_CHANGED_MIND"},
        headers=auth(OWNER_TOKEN),
    )

    cancelled = client.get(f"/track/{created['tracking_code']}")
    unknown = client.get("/track/SHP-20260914-000000")

    assert cancelled.status_code == unknown.status_code == 404
    assert cancelled.json() == unknown.json()


def test_a_malformed_code_is_404_rather_than_an_error() -> None:
    client, _ = build_client()
    assert client.get("/track/not-a-code").status_code == 404
