"""Merchant HTTP adapter: authorization, scope and the MER-02 surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from merchant_fixtures import (
    APPLICANT,
    CONFIGURED_ATTRIBUTES,
    MEMBER,
    OUTSIDER,
    REVIEWER,
    valid_attributes,
)

from merchant.config import (
    MerchantSettings,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
)
from merchant.infrastructure.authorizers.identity import FakeMerchantAuthorizer
from merchant.infrastructure.memory import InMemoryMerchantUnitOfWork
from merchant.main import create_app
from merchant.ports.authorization import MerchantActor, MerchantRole

OWNER_TOKEN = "owner-token"
OPS_TOKEN = "ops-token"
MEMBER_TOKEN = "member-token"
OUTSIDER_TOKEN = "outsider-token"


def build_client(
    *, required_attributes: tuple[str, ...] = CONFIGURED_ATTRIBUTES, **authorizer_kwargs
) -> tuple[TestClient, InMemoryMerchantUnitOfWork]:
    uow = InMemoryMerchantUnitOfWork()
    authorizer = FakeMerchantAuthorizer(
        token_actors={
            OWNER_TOKEN: MerchantActor(
                principal_id=APPLICANT, roles=frozenset({MerchantRole.CUSTOMER})
            ),
            OPS_TOKEN: MerchantActor(
                principal_id=REVIEWER, roles=frozenset({MerchantRole.OPERATIONS})
            ),
            MEMBER_TOKEN: MerchantActor(
                principal_id=MEMBER, roles=frozenset({MerchantRole.MERCHANT_MEMBER})
            ),
            OUTSIDER_TOKEN: MerchantActor(
                principal_id=OUTSIDER, roles=frozenset({MerchantRole.CUSTOMER})
            ),
        },
        **authorizer_kwargs,
    )
    app = create_app(
        MerchantSettings(
            environment=RuntimeEnvironment.TEST,
            application_required_attributes=required_attributes,
        ),
        unit_of_work=uow,
        authorizer=authorizer,
    )
    return TestClient(app), uow


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def approve_merchant(client: TestClient) -> str:
    created = client.post(
        "/merchant/applications", json={"attributes": valid_attributes()}, headers=auth(OWNER_TOKEN)
    )
    application_id = created.json()["application_id"]
    client.post(f"/merchant/applications/{application_id}/submit", headers=auth(OWNER_TOKEN))
    client.post(
        f"/merchant/applications/{application_id}/decision",
        json={"decision": "APPROVED", "display_name": "Karbala Home Goods"},
        headers=auth(OPS_TOKEN),
    )
    # The merchant id is not in the application response, so read it the way a client would.
    uow = client.app.state.unit_of_work
    uow.begin()
    merchant = uow.merchants.find_by_owner(APPLICANT)
    uow.commit()
    return str(merchant.merchant_id)


def create_store(client: TestClient, merchant_id: str, name: str = "Center") -> str:
    response = client.post(
        f"/merchant/merchants/{merchant_id}/stores",
        json={"name": name, "governorate": "KARBALA", "address_line": "Al-Nuqabat 8"},
        headers=auth(OWNER_TOKEN),
    )
    assert response.status_code == 201, response.text
    return response.json()["store_id"]


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401_not_422() -> None:
    client, _ = build_client()
    assert client.post("/merchant/applications", json={"attributes": {}}).status_code == 401


def test_an_unknown_token_is_401() -> None:
    client, _ = build_client()
    response = client.post(
        "/merchant/applications", json={"attributes": {}}, headers=auth("forged")
    )
    assert response.status_code == 401


def test_a_non_bearer_header_is_401() -> None:
    client, _ = build_client()
    response = client.post(
        "/merchant/applications", json={"attributes": {}}, headers={"Authorization": OWNER_TOKEN}
    )
    assert response.status_code == 401


def test_an_identity_outage_is_503_not_a_denial() -> None:
    client, _ = build_client(unavailable=True)
    response = client.post(
        "/merchant/applications", json={"attributes": {}}, headers=auth(OWNER_TOKEN)
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "authorization_unavailable"


def test_the_default_composition_denies_everything() -> None:
    """No configured Identity means no caller can be proven, so none is admitted."""
    app = create_app(
        MerchantSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryMerchantUnitOfWork(),
    )
    client = TestClient(app)
    response = client.post(
        "/merchant/applications", json={"attributes": {}}, headers=auth(OWNER_TOKEN)
    )
    assert response.status_code == 401


def test_production_refuses_to_start_without_identity() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            MerchantSettings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://x/y",
            )
        )
    assert "MERCHANT_IDENTITY_BASE_URL" in str(caught.value)


# ------------------------------------------------------------------ MER-02


def test_requirements_report_the_open_item_when_undecided() -> None:
    client, _ = build_client(required_attributes=())
    response = client.get("/merchant/applications/requirements", headers=auth(OWNER_TOKEN))
    body = response.json()

    assert body["submission_enabled"] is False
    assert body["required_attributes"] == []
    assert "open item" in body["blocked_reason"]


def test_submitting_while_undecided_is_409_with_the_open_item_named() -> None:
    client, _ = build_client(required_attributes=())
    created = client.post(
        "/merchant/applications", json={"attributes": {}}, headers=auth(OWNER_TOKEN)
    )
    application_id = created.json()["application_id"]

    response = client.post(
        f"/merchant/applications/{application_id}/submit", headers=auth(OWNER_TOKEN)
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "merchant_application_data_set_not_defined"
    assert detail["open_item"] == "MER-02"


def test_readiness_reports_the_undecided_data_set() -> None:
    client, _ = build_client(required_attributes=())
    body = client.get("/ready").json()

    assert body["checks"]["merchant_application_data_set_decided"] is False
    assert "merchant_application_data_set_decided" in body["blockers"]


def test_readiness_clears_once_the_data_set_is_configured() -> None:
    client, _ = build_client()
    body = client.get("/ready").json()

    assert body["checks"]["merchant_application_data_set_decided"] is True


def test_missing_attributes_are_named_in_the_422() -> None:
    client, _ = build_client()
    created = client.post(
        "/merchant/applications",
        json={"attributes": {"business_name": "Only this"}},
        headers=auth(OWNER_TOKEN),
    )
    application_id = created.json()["application_id"]

    response = client.post(
        f"/merchant/applications/{application_id}/submit", headers=auth(OWNER_TOKEN)
    )

    assert response.status_code == 422
    assert set(response.json()["detail"]["missing"]) == {
        "business_type",
        "business_phone",
        "city",
    }


# ------------------------------------------------------------------ application


def test_an_applicant_cannot_read_another_applicants_case() -> None:
    client, _ = build_client()
    created = client.post(
        "/merchant/applications", json={"attributes": {}}, headers=auth(OWNER_TOKEN)
    )
    application_id = created.json()["application_id"]

    response = client.patch(
        f"/merchant/applications/{application_id}",
        json={"attributes": {"city": "NAJAF"}},
        headers=auth(OUTSIDER_TOKEN),
    )

    # 404 rather than 403 — confirming the id exists would leak someone else's case.
    assert response.status_code == 404


def test_an_applicant_cannot_decide_their_own_application() -> None:
    client, _ = build_client()
    created = client.post(
        "/merchant/applications",
        json={"attributes": valid_attributes()},
        headers=auth(OWNER_TOKEN),
    )
    application_id = created.json()["application_id"]
    client.post(f"/merchant/applications/{application_id}/submit", headers=auth(OWNER_TOKEN))

    response = client.post(
        f"/merchant/applications/{application_id}/decision",
        json={"decision": "APPROVED", "display_name": "Self approved"},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 403


def test_approval_needs_a_display_name() -> None:
    client, _ = build_client()
    created = client.post(
        "/merchant/applications",
        json={"attributes": valid_attributes()},
        headers=auth(OWNER_TOKEN),
    )
    application_id = created.json()["application_id"]
    client.post(f"/merchant/applications/{application_id}/submit", headers=auth(OWNER_TOKEN))

    response = client.post(
        f"/merchant/applications/{application_id}/decision",
        json={"decision": "APPROVED"},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 422


def test_the_full_application_flow_activates_a_merchant() -> None:
    client, uow = build_client()
    merchant_id = approve_merchant(client)

    response = client.get(f"/merchant/merchants/{merchant_id}", headers=auth(OWNER_TOKEN))

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    assert len(response.json()["merchant_code"]) == 8


# ------------------------------------------------------------------ policy


def test_a_merchant_can_turn_on_the_add_ons_they_own() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.patch(
        f"/merchant/merchants/{merchant_id}/policy",
        json={"open_box_allowed": True, "photo_documentation_enabled": True},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["open_box_allowed"] is True


def test_setting_a_company_wide_rule_is_403_and_names_the_key() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.patch(
        f"/merchant/merchants/{merchant_id}/policy",
        json={"open_box_allowed": True, "hold_period_days": 7},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["keys"] == ["hold_period_days"]


def test_a_rejected_policy_key_changes_nothing() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    client.patch(
        f"/merchant/merchants/{merchant_id}/policy",
        json={"open_box_allowed": True, "liability_position": "MERCHANT"},
        headers=auth(OWNER_TOKEN),
    )

    policy = client.get(
        f"/merchant/merchants/{merchant_id}/policy", headers=auth(OWNER_TOKEN)
    ).json()
    assert policy["open_box_allowed"] is False


def test_the_policy_response_shows_the_rules_a_sender_cannot_change() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    fixed = client.get(
        f"/merchant/merchants/{merchant_id}/policy", headers=auth(OWNER_TOKEN)
    ).json()["platform_fixed"]

    assert fixed["hold_period_days"] == 3
    assert fixed["return_fee_owner"] == "MERCHANT"


def test_an_outsider_cannot_read_a_merchants_policy() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.get(
        f"/merchant/merchants/{merchant_id}/policy", headers=auth(OUTSIDER_TOKEN)
    )

    assert response.status_code == 403


# ------------------------------------------------------------------ stores


def test_the_owner_can_open_a_branch() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    store_id = create_store(client, merchant_id)

    stores = client.get(
        f"/merchant/merchants/{merchant_id}/stores", headers=auth(OWNER_TOKEN)
    ).json()
    assert [store["store_id"] for store in stores] == [store_id]
    assert stores[0]["is_default_pickup"] is True


def test_an_outsider_cannot_open_a_branch() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/stores",
        json={"name": "Center", "governorate": "KARBALA", "address_line": "x"},
        headers=auth(OUTSIDER_TOKEN),
    )

    assert response.status_code == 403


def test_an_unknown_governorate_is_422() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/stores",
        json={"name": "Center", "governorate": "ATLANTIS", "address_line": "x"},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ team


def test_an_invitation_reveals_only_the_last_four_digits() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    )

    body = response.json()
    assert response.status_code == 201
    assert body["phone_last4"] == "4567"
    assert "7701234" not in response.text


def test_a_pending_invitation_reports_no_permissions() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)

    body = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()

    assert body["status"] == "PENDING"
    assert body["permissions"] == []


def test_the_invitee_accepts_in_their_own_account() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)
    membership_id = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()["membership_id"]

    response = client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    assert sorted(response.json()["permissions"]) == [
        "courier_handover:complete",
        "store_parcel:prepare",
        "store_parcel:read",
    ]


def test_a_member_cannot_invite_colleagues() -> None:
    """A warehouse keeper prepares parcels; they do not run the store."""
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)
    membership_id = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()["membership_id"]
    client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )

    response = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647705554444", "store_ids": [store_id]},
        headers=auth(MEMBER_TOKEN),
    )

    assert response.status_code == 403


def test_a_member_cannot_change_the_standing_policy() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)
    membership_id = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()["membership_id"]
    client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )

    response = client.patch(
        f"/merchant/merchants/{merchant_id}/policy",
        json={"open_box_allowed": True},
        headers=auth(MEMBER_TOKEN),
    )

    assert response.status_code == 403


def test_an_accepted_member_can_read_the_store_list() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)
    membership_id = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()["membership_id"]
    client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )

    response = client.get(
        f"/merchant/merchants/{merchant_id}/stores", headers=auth(MEMBER_TOKEN)
    )

    assert response.status_code == 200


def test_store_access_is_readable_by_its_own_principal() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    store_id = create_store(client, merchant_id)
    membership_id = client.post(
        f"/merchant/merchants/{merchant_id}/team",
        json={"phone": "+9647701234567", "store_ids": [store_id]},
        headers=auth(OWNER_TOKEN),
    ).json()["membership_id"]
    client.post(
        f"/merchant/team/{membership_id}/respond",
        json={"accept": True},
        headers=auth(MEMBER_TOKEN),
    )

    response = client.get(
        f"/merchant/principals/{MEMBER}/store-access", headers=auth(MEMBER_TOKEN)
    )

    assert response.status_code == 200
    assert response.json()[0]["store_ids"] == [store_id]


def test_store_access_of_another_principal_needs_operations() -> None:
    client, _ = build_client()

    denied = client.get(
        f"/merchant/principals/{MEMBER}/store-access", headers=auth(OUTSIDER_TOKEN)
    )
    allowed = client.get(
        f"/merchant/principals/{MEMBER}/store-access", headers=auth(OPS_TOKEN)
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200


# ------------------------------------------------------------------ labels


def test_a_merchant_cannot_issue_their_own_label_stock() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={"batch_reference": "ROLL-001", "label_count": 50},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 403


def test_operations_issues_stock_and_the_merchant_sees_the_count() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={"batch_reference": "ROLL-001", "label_count": 50},
        headers=auth(OPS_TOKEN),
    )

    summary = client.get(
        f"/merchant/merchants/{merchant_id}/label-stock", headers=auth(OWNER_TOKEN)
    ).json()

    assert summary["total_labels"] == 50
    assert summary["remaining"] == 50


def test_self_printed_stock_without_a_printer_is_403() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={
            "batch_reference": "SELF-001",
            "label_count": 10,
            "source": "MERCHANT_SELF_PRINTED",
        },
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "self_printing_not_authorized"


def test_there_is_no_route_that_prints_a_label_for_a_shipment() -> None:
    client, _ = build_client()
    paths = client.get("/openapi.json").json()["paths"]

    assert not [path for path in paths if "print" in path and "authorization" not in path]


# ------------------------------------------------------------------ catalogue


def test_a_merchant_can_save_products_and_categories() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    category_id = client.post(
        f"/merchant/merchants/{merchant_id}/categories",
        json={"name": "Bedding"},
        headers=auth(OWNER_TOKEN),
    ).json()["category_id"]

    response = client.post(
        f"/merchant/merchants/{merchant_id}/products",
        json={"name": "Cotton bedsheet set", "category_id": category_id},
        headers=auth(OWNER_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["category_id"] == category_id


def test_deleting_a_category_in_use_is_409() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    category_id = client.post(
        f"/merchant/merchants/{merchant_id}/categories",
        json={"name": "Bedding"},
        headers=auth(OWNER_TOKEN),
    ).json()["category_id"]
    client.post(
        f"/merchant/merchants/{merchant_id}/products",
        json={"name": "Cotton bedsheet set", "category_id": category_id},
        headers=auth(OWNER_TOKEN),
    )

    response = client.delete(
        f"/merchant/categories/{category_id}", headers=auth(OWNER_TOKEN)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["product_count"] == 1


def test_seal_stock_is_reported_separately_over_http() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)
    client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={"batch_reference": "ROLL-001", "label_count": 50},
        headers=auth(OPS_TOKEN),
    )
    client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={
            "batch_reference": "SEAL-001",
            "label_count": 200,
            "stock_kind": "PACKAGING_SEAL",
        },
        headers=auth(OPS_TOKEN),
    )

    summary = client.get(
        f"/merchant/merchants/{merchant_id}/label-stock", headers=auth(OWNER_TOKEN)
    ).json()

    assert summary["total_labels"] == 50
    assert summary["total_seals"] == 200


def test_a_self_printed_packaging_seal_is_refused_over_http() -> None:
    client, _ = build_client()
    merchant_id = approve_merchant(client)

    response = client.post(
        f"/merchant/merchants/{merchant_id}/label-stock",
        json={
            "batch_reference": "SEAL-002",
            "label_count": 100,
            "stock_kind": "PACKAGING_SEAL",
            "source": "MERCHANT_SELF_PRINTED",
        },
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 403
