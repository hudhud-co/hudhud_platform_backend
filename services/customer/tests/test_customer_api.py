"""Customer HTTP adapter: ownership isolation, authorization and error mapping."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from customer_fixtures import OTHER_PRINCIPAL, PRINCIPAL
from fastapi.testclient import TestClient

from customer.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from customer.infrastructure.authorizers import (
    DefaultDenyCustomerAuthorizer,
    FakeCustomerAuthorizer,
    build_actor,
)
from customer.infrastructure.memory import InMemoryCustomerUnitOfWork
from customer.main import create_app
from customer.ports.authorization import CustomerActor, CustomerCommand, CustomerRole

TOKEN = "customer-token"  # noqa: S105 - test fixture
OTHER_TOKEN = "other-customer-token"  # noqa: S105 - test fixture

ACTORS = {
    TOKEN: CustomerActor(
        principal_id=PRINCIPAL, roles=frozenset({CustomerRole.CUSTOMER})
    ),
    OTHER_TOKEN: CustomerActor(
        principal_id=OTHER_PRINCIPAL, roles=frozenset({CustomerRole.CUSTOMER})
    ),
}


@pytest.fixture
def store() -> InMemoryCustomerUnitOfWork:
    return InMemoryCustomerUnitOfWork()


@pytest.fixture
def client(store: InMemoryCustomerUnitOfWork) -> Iterator[TestClient]:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=store,
        authorizer=FakeCustomerAuthorizer(token_actors=ACTORS),
    )
    with TestClient(app) as test_client:
        yield test_client


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------ authentication


def test_every_route_requires_a_bearer_token(client: TestClient) -> None:
    assert client.get("/customer/me").status_code == 401
    assert client.get("/customer/me/addresses").status_code == 401
    assert (
        client.post(
            "/customer/me/contacts",
            json={"phone": "+9647701820934", "governorate": "BAGHDAD"},
        ).status_code
        == 401
    )


def test_an_unknown_token_is_unauthenticated(client: TestClient) -> None:
    assert client.get("/customer/me", headers=_auth("nope")).status_code == 401


def test_a_denied_command_is_forbidden(store: InMemoryCustomerUnitOfWork) -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=store,
        authorizer=FakeCustomerAuthorizer(
            token_actors=ACTORS,
            denied_commands=frozenset({CustomerCommand.ADDRESS_CREATE}),
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            "/customer/me/addresses",
            json={"governorate": "BAGHDAD", "line": "x"},
            headers=_auth(),
        )

    assert response.status_code == 403


# ------------------------------------------------------------------ profile


def test_reading_the_profile_creates_it_on_first_contact(client: TestClient) -> None:
    response = client.get("/customer/me", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["completion_state"] == "INCOMPLETE"
    assert body["can_use_the_app"] is False
    assert len(body["outstanding_documents"]) == 2


def test_the_full_first_run_flow_makes_the_app_usable(client: TestClient) -> None:
    """authName → authTerms → authNotify → authDone."""
    client.get("/customer/me", headers=_auth())
    client.post(
        "/customer/me/display-name", json={"display_name": "Layla"}, headers=_auth()
    )
    for kind in ("TERMS_OF_SERVICE", "PRIVACY_POLICY"):
        accepted = client.post(
            "/customer/me/legal-acceptances",
            json={"kind": kind, "document_version": "1.0"},
            headers=_auth(),
        )
        assert accepted.status_code == 200

    final = client.get("/customer/me", headers=_auth()).json()

    assert final["completion_state"] == "COMPLETE"
    assert final["can_use_the_app"] is True


def test_accepting_a_stale_document_version_is_a_conflict(client: TestClient) -> None:
    response = client.post(
        "/customer/me/legal-acceptances",
        json={"kind": "TERMS_OF_SERVICE", "document_version": "0.9"},
        headers=_auth(),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legal_document_not_accepted"
    assert response.json()["detail"]["required_version"] == "1.0"


def test_a_blank_display_name_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/customer/me/display-name", json={"display_name": ""}, headers=_auth()
    )

    assert response.status_code == 422


def test_an_unknown_notification_channel_is_rejected(client: TestClient) -> None:
    client.get("/customer/me", headers=_auth())

    response = client.post(
        "/customer/me/notification-preferences",
        json={"channels": ["PIGEON"]},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_notification_channel"


# ----------------------------------------------------------------- contacts


def test_a_contact_needs_only_a_phone_and_a_governorate(client: TestClient) -> None:
    response = client.post(
        "/customer/me/contacts",
        json={"phone": "+9647701820934", "governorate": "BAGHDAD"},
        headers=_auth(),
    )

    assert response.status_code == 201
    assert response.json()["display_name"] is None


def test_an_unknown_governorate_is_unprocessable(client: TestClient) -> None:
    response = client.post(
        "/customer/me/contacts",
        json={"phone": "+9647701820934", "governorate": "ATLANTIS"},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_governorate"


def test_contacts_are_scoped_to_their_owner(client: TestClient) -> None:
    client.post(
        "/customer/me/contacts",
        json={"phone": "+9647701820934", "governorate": "BAGHDAD"},
        headers=_auth(),
    )

    mine = client.get("/customer/me/contacts", headers=_auth()).json()
    theirs = client.get("/customer/me/contacts", headers=_auth(OTHER_TOKEN)).json()

    assert len(mine) == 1
    assert theirs == []


# ---------------------------------------------------------------- addresses


def test_the_first_address_is_the_default(client: TestClient) -> None:
    response = client.post(
        "/customer/me/addresses",
        json={"governorate": "BAGHDAD", "line": "Al-Jadriya 14"},
        headers=_auth(),
    )

    assert response.status_code == 201
    assert response.json()["is_default"] is True


def test_another_customer_cannot_touch_my_address(client: TestClient) -> None:
    created = client.post(
        "/customer/me/addresses",
        json={"governorate": "BAGHDAD", "line": "Al-Jadriya 14"},
        headers=_auth(),
    ).json()

    response = client.post(
        f"/customer/me/addresses/{created['address_id']}/default",
        headers=_auth(OTHER_TOKEN),
    )

    # 404, not 403: confirming the id exists would leak another customer's data.
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "address_not_found"


def test_archiving_the_only_address_is_a_conflict(client: TestClient) -> None:
    created = client.post(
        "/customer/me/addresses",
        json={"governorate": "BAGHDAD", "line": "Al-Jadriya 14"},
        headers=_auth(),
    ).json()

    response = client.post(
        f"/customer/me/addresses/{created['address_id']}/archive", headers=_auth()
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "last_address_cannot_be_archived"


def test_an_invalid_map_pin_is_unprocessable(client: TestClient) -> None:
    response = client.post(
        "/customer/me/addresses",
        json={
            "governorate": "BAGHDAD",
            "line": "Al-Jadriya 14",
            "geo": {"latitude": "120.0", "longitude": "44.3"},
        },
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_geo_point"


def test_addresses_can_be_filtered_by_kind(client: TestClient) -> None:
    client.post(
        "/customer/me/addresses",
        json={"kind": "DELIVERY", "governorate": "BAGHDAD", "line": "Home"},
        headers=_auth(),
    )
    client.post(
        "/customer/me/addresses",
        json={"kind": "PICKUP", "governorate": "BAGHDAD", "line": "Store"},
        headers=_auth(),
    )

    pickup = client.get("/customer/me/addresses?kind=PICKUP", headers=_auth()).json()

    assert len(pickup) == 1
    assert pickup[0]["kind"] == "PICKUP"


def test_an_unknown_address_id_is_not_found(client: TestClient) -> None:
    response = client.post(
        f"/customer/me/addresses/{uuid4()}/default", headers=_auth()
    )

    assert response.status_code == 404


# ------------------------------------------------------- composition gates


def test_the_service_is_fail_closed_without_identity() -> None:
    authorizer = DefaultDenyCustomerAuthorizer()

    assert authorizer.is_production_ready is False


def test_production_requires_a_database_and_identity() -> None:
    with pytest.raises(ProductionStartupBlockedError) as excinfo:
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url=None,
            identity_base_url=None,
            identity_service_credential=None,
        ).assert_production_gates()

    assert "CUSTOMER_DATABASE_URL" in str(excinfo.value)
    assert "CUSTOMER_IDENTITY_BASE_URL" in str(excinfo.value)


def test_readiness_reports_default_deny_authorization(
    store: InMemoryCustomerUnitOfWork,
) -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST), unit_of_work=store
    )
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert "authorization_configured" in response.json()["blockers"]


# ------------------------------------------------- identity actor mapping


def test_an_identity_body_maps_to_a_customer_actor() -> None:
    actor = build_actor(
        {
            "active": True,
            "principal_id": str(PRINCIPAL),
            "status": "ACTIVE",
            "roles": ["CUSTOMER", "PICKUP_DRIVER"],
        }
    )

    assert actor is not None
    assert actor.principal_id == PRINCIPAL
    # PICKUP_DRIVER means nothing here and must not be carried across.
    assert actor.roles == frozenset({CustomerRole.CUSTOMER})


def test_a_suspended_principal_maps_to_nothing() -> None:
    assert (
        build_actor(
            {"active": True, "principal_id": str(PRINCIPAL), "status": "SUSPENDED"}
        )
        is None
    )


def test_an_inactive_body_maps_to_nothing() -> None:
    assert build_actor({"active": False}) is None
    assert build_actor({"active": True, "principal_id": "not-a-uuid"}) is None
