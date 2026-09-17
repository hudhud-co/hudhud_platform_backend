"""Identity HTTP adapter: secrecy of responses, status mapping and admin authorization."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from identity_fixtures import PHONE, SERVICE_CREDENTIAL, SIGNING_KEY

from identity.application.role_service import GrantRoleCommand, RoleService
from identity.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from identity.domain.value_objects import Role
from identity.infrastructure.adapters import (
    ConsoleOtpDelivery,
    RecordingOtpDelivery,
    SharedSecretServiceCredentials,
)
from identity.infrastructure.memory import InMemoryIdentityUnitOfWork
from identity.main import create_app

SVC = {"X-Service-Credential": SERVICE_CREDENTIAL}


@pytest.fixture
def store() -> InMemoryIdentityUnitOfWork:
    return InMemoryIdentityUnitOfWork()


@pytest.fixture
def delivery() -> RecordingOtpDelivery:
    return RecordingOtpDelivery()


@pytest.fixture
def client(
    store: InMemoryIdentityUnitOfWork, delivery: RecordingOtpDelivery
) -> Iterator[TestClient]:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=SIGNING_KEY),
        unit_of_work=store,
        otp_delivery=delivery,
        service_credentials=SharedSecretServiceCredentials({"pickup": SERVICE_CREDENTIAL}),
    )
    with TestClient(app) as test_client:
        yield test_client


def _sign_in(client: TestClient, delivery: RecordingOtpDelivery, phone: str = PHONE) -> dict:
    issued = client.post("/identity/otp/request", json={"phone": phone})
    assert issued.status_code == 200
    challenge_id = issued.json()["challenge_id"]
    verified = client.post(
        "/identity/otp/verify",
        json={"challenge_id": challenge_id, "code": delivery.code_for(challenge_id)},
    )
    assert verified.status_code == 200
    return verified.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ----------------------------------------------------------------- secrecy


def test_the_otp_response_never_contains_the_code(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    response = client.post("/identity/otp/request", json={"phone": PHONE})

    body = response.json()
    code = delivery.code_for(body["challenge_id"])
    assert code not in response.text
    assert set(body) == {"challenge_id", "phone_last4", "expires_at"}


def test_the_otp_response_does_not_reveal_whether_the_phone_is_registered(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    """Two requests for the same number must be indistinguishable to the caller."""
    first = client.post("/identity/otp/request", json={"phone": PHONE})
    second = client.post("/identity/otp/request", json={"phone": PHONE})

    assert set(first.json()) == set(second.json())
    assert "principal_created" not in first.text


def test_only_the_last_four_digits_of_the_phone_come_back(client: TestClient) -> None:
    response = client.post("/identity/otp/request", json={"phone": PHONE})

    assert response.json()["phone_last4"] == "0934"
    assert PHONE not in response.text


def test_every_authentication_failure_returns_the_same_opaque_answer(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    issued = client.post("/identity/otp/request", json={"phone": PHONE}).json()

    wrong_code = client.post(
        "/identity/otp/verify",
        json={"challenge_id": issued["challenge_id"], "code": "000000"},
    )
    unknown_challenge = client.post(
        "/identity/otp/verify",
        json={"challenge_id": str(uuid4()), "code": "000000"},
    )

    assert wrong_code.status_code == unknown_challenge.status_code == 401
    assert wrong_code.json()["detail"] == unknown_challenge.json()["detail"]
    assert wrong_code.json()["detail"]["code"] == "authentication_failed"


# ------------------------------------------------------------- happy path


def test_a_successful_sign_in_returns_a_bearer_session(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)

    assert session["token_type"] == "Bearer"
    assert session["roles"] == ["CUSTOMER"]
    assert session["status"] == "ACTIVE"


def test_me_returns_the_signed_in_principal(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)

    response = client.get(
        "/identity/me", headers={**_auth(session["access_token"]), **SVC}
    )

    assert response.status_code == 200
    assert response.json()["principal_id"] == session["principal_id"]
    assert response.json()["phone_last4"] == "0934"


def test_me_without_a_token_is_unauthenticated(client: TestClient) -> None:
    assert client.get("/identity/me", headers=SVC).status_code == 401


# ----------------------------------------------------------- introspection


def test_introspection_requires_a_service_credential(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)

    response = client.post(
        "/identity/tokens/introspect", json={"token": session["access_token"]}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "service_credential_rejected"


def test_introspection_resolves_a_live_token_for_a_service(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)

    response = client.post(
        "/identity/tokens/introspect",
        json={"token": session["access_token"]},
        headers=SVC,
    )

    body = response.json()
    assert body["active"] is True
    assert body["principal_id"] == session["principal_id"]
    assert body["roles"] == ["CUSTOMER"]


def test_introspection_of_a_revoked_token_reports_inactive(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)
    client.post(
        "/identity/sessions/revoke",
        headers={**_auth(session["access_token"]), **SVC},
    )

    response = client.post(
        "/identity/tokens/introspect",
        json={"token": session["access_token"]},
        headers=SVC,
    )

    assert response.json()["active"] is False
    assert response.json()["principal_id"] is None


# --------------------------------------------------------- administration


def test_a_customer_cannot_grant_themselves_a_role(
    client: TestClient, delivery: RecordingOtpDelivery
) -> None:
    session = _sign_in(client, delivery)

    response = client.post(
        f"/identity/principals/{session['principal_id']}/roles",
        json={"role": "OPERATIONS"},
        headers={**_auth(session["access_token"]), **SVC},
    )

    assert response.status_code == 403


def test_operations_can_grant_a_driver_role(
    client: TestClient, delivery: RecordingOtpDelivery, store: InMemoryIdentityUnitOfWork
) -> None:
    ops = _sign_in(client, delivery, phone="+9647000000001")
    driver = _sign_in(client, delivery, phone="+9647000000002")
    _make_operations(store, ops["principal_id"])

    response = client.post(
        f"/identity/principals/{driver['principal_id']}/roles",
        json={"role": "PICKUP_DRIVER"},
        headers={**_auth(ops["access_token"]), **SVC},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "PICKUP_DRIVER"


def test_granting_a_merchant_role_without_a_scope_is_rejected(
    client: TestClient, delivery: RecordingOtpDelivery, store: InMemoryIdentityUnitOfWork
) -> None:
    ops = _sign_in(client, delivery, phone="+9647000000001")
    member = _sign_in(client, delivery, phone="+9647000000002")
    _make_operations(store, ops["principal_id"])

    response = client.post(
        f"/identity/principals/{member['principal_id']}/roles",
        json={"role": "MERCHANT_MEMBER"},
        headers={**_auth(ops["access_token"]), **SVC},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "role_grant_scope_invalid"


def test_suspending_a_principal_ends_its_sessions(
    client: TestClient, delivery: RecordingOtpDelivery, store: InMemoryIdentityUnitOfWork
) -> None:
    ops = _sign_in(client, delivery, phone="+9647000000001")
    victim = _sign_in(client, delivery, phone="+9647000000002")
    _make_operations(store, ops["principal_id"])

    response = client.post(
        f"/identity/principals/{victim['principal_id']}/status",
        json={"status": "SUSPENDED", "reason": "lateness block"},
        headers={**_auth(ops["access_token"]), **SVC},
    )

    assert response.status_code == 204
    introspected = client.post(
        "/identity/tokens/introspect",
        json={"token": victim["access_token"]},
        headers=SVC,
    )
    assert introspected.json()["active"] is False


def _make_operations(store: InMemoryIdentityUnitOfWork, principal_id: str) -> None:
    RoleService(store).grant(
        GrantRoleCommand(
            principal_id=UUID(principal_id),
            role=Role.OPERATIONS,
            granted_by="bootstrap",
            occurred_at=datetime.now(tz=UTC),
        )
    )


# -------------------------------------------------------- production gates


def test_production_requires_a_signing_key_and_service_credentials() -> None:
    with pytest.raises(ProductionStartupBlockedError) as excinfo:
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql://x/y",
            signing_key=None,
            service_credentials={},
        ).assert_production_gates()

    assert "IDENTITY_SIGNING_KEY" in str(excinfo.value)
    assert "IDENTITY_SERVICE_CREDENTIALS" in str(excinfo.value)


def test_without_a_signing_key_the_authentication_routes_are_not_mounted() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=None),
        unit_of_work=InMemoryIdentityUnitOfWork(),
    )

    assert "/identity/otp/request" not in app.openapi()["paths"]


def test_readiness_reports_the_missing_pieces() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST, signing_key=None),
        unit_of_work=InMemoryIdentityUnitOfWork(),
    )
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert "signing_key_configured" in response.json()["blockers"]
    assert "otp_delivery_configured" in response.json()["blockers"]


def test_console_otp_delivery_is_refused_outside_local_and_test() -> None:
    """A retained log must never become an authentication bypass."""
    for environment in (RuntimeEnvironment.STAGING, RuntimeEnvironment.PRODUCTION):
        with pytest.raises(ProductionStartupBlockedError):
            load_settings(environment=environment, otp_delivery_channel="console")


def test_console_otp_delivery_is_never_production_ready() -> None:
    assert ConsoleOtpDelivery().is_production_ready is False
