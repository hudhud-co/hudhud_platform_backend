"""JWT/JWKS query authorization tests."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from conftest import SHIPMENT_ID
from fastapi.testclient import TestClient
from jwt_test_support import (
    SENSITIVE_TOKEN_MARKER,
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_KID,
    TEST_SUBJECT,
    AllowShipmentAccessPolicy,
    DenyShipmentAccessPolicy,
    FakeJwksClient,
    generate_signing_material,
    jwks_key_from_material,
    mint_access_token,
)

from tracking.application.query import TimelineQueryService
from tracking.config import JwtSettings, RuntimeEnvironment, load_settings
from tracking.infrastructure.authorizers.jwt_query_authorizer import JwtQueryAuthorizer
from tracking.infrastructure.jwt.verifier import JwtVerifier
from tracking.infrastructure.memory import MemoryTrackingStore
from tracking.main import create_app
from tracking.ports.query_authorizer import (
    AuthorizerUnavailableError,
    TrackingAccessDecision,
    TrackingAuthorizationOutcome,
)

OTHER_KID = "other-key"


@pytest.fixture
def signing_material():
    return generate_signing_material()


@pytest.fixture
def jwks_client(signing_material):
    key = jwks_key_from_material(signing_material)
    return FakeJwksClient(keys_by_kid={signing_material.kid: key})


@pytest.fixture
def verifier(jwks_client):
    return JwtVerifier(
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        allowed_algorithms=("RS256",),
        jwks_client=jwks_client,
    )


@pytest.fixture
def allow_policy():
    return AllowShipmentAccessPolicy(allowed_subjects={TEST_SUBJECT})


@pytest.fixture
def jwt_authorizer(verifier, allow_policy, jwks_client):
    return JwtQueryAuthorizer(
        verifier=verifier,
        shipment_policy=allow_policy,
        jwks_available=True,
    )


def _jwt_settings(**overrides: object) -> JwtSettings:
    base = {
        "issuer": TEST_ISSUER,
        "audience": TEST_AUDIENCE,
        "jwks_url": "https://identity.example.test/.well-known/jwks.json",
        "allowed_algorithms": ("RS256",),
    }
    base.update(overrides)
    return JwtSettings(**base)  # type: ignore[arg-type]


async def _authorize(
    authorizer: JwtQueryAuthorizer,
    token: str,
    shipment_id: UUID = SHIPMENT_ID,
) -> TrackingAccessDecision:
    return await authorizer.authorize_timeline_read(
        bearer_token=token,
        shipment_id=shipment_id,
    )


def _run_authorize(
    authorizer: JwtQueryAuthorizer,
    token: str,
    shipment_id: UUID = SHIPMENT_ID,
) -> TrackingAccessDecision:
    return asyncio.run(_authorize(authorizer, token, shipment_id))


def test_valid_signed_token_plus_allowed_shipment(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    token = mint_access_token(signing_material)
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.ALLOWED


def test_valid_token_plus_denied_shipment(
    verifier: JwtVerifier,
    signing_material,
) -> None:
    authorizer = JwtQueryAuthorizer(
        verifier=verifier,
        shipment_policy=DenyShipmentAccessPolicy(),
        jwks_available=True,
    )
    token = mint_access_token(signing_material)
    decision = _run_authorize(authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.FORBIDDEN


def test_missing_token_unauthenticated(jwt_authorizer: JwtQueryAuthorizer) -> None:
    decision = _run_authorize(jwt_authorizer, "")
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_malformed_token_unauthenticated(jwt_authorizer: JwtQueryAuthorizer) -> None:
    decision = _run_authorize(jwt_authorizer, "not-a-jwt")
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_invalid_signature_unauthenticated(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    other = generate_signing_material(kid=TEST_KID)
    token = mint_access_token(other, kid=TEST_KID)
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_invalid_issuer_unauthenticated(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    token = mint_access_token(signing_material, issuer="https://wrong.example")
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_invalid_audience_unauthenticated(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    token = mint_access_token(signing_material, audience="other-service")
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_expired_token_unauthenticated(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    token = mint_access_token(signing_material, expires_in_seconds=-30)
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_not_yet_valid_token_unauthenticated(
    jwt_authorizer: JwtQueryAuthorizer,
    signing_material,
) -> None:
    token = mint_access_token(signing_material, not_before_offset_seconds=120)
    decision = _run_authorize(jwt_authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_disallowed_algorithm_unauthenticated(
    jwks_client: FakeJwksClient,
    signing_material,
) -> None:
    verifier = JwtVerifier(
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        allowed_algorithms=("ES256",),
        jwks_client=jwks_client,
    )
    authorizer = JwtQueryAuthorizer(
        verifier=verifier,
        shipment_policy=AllowShipmentAccessPolicy(allowed_subjects={TEST_SUBJECT}),
        jwks_available=True,
    )
    token = mint_access_token(signing_material, algorithm="RS256")
    decision = _run_authorize(authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED


def test_unknown_kid_triggers_single_refresh(
    signing_material,
) -> None:
    client = FakeJwksClient(
        keys_by_kid={signing_material.kid: jwks_key_from_material(signing_material)}
    )
    verifier = JwtVerifier(
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        allowed_algorithms=("RS256",),
        jwks_client=client,
    )
    authorizer = JwtQueryAuthorizer(
        verifier=verifier,
        shipment_policy=AllowShipmentAccessPolicy(allowed_subjects={TEST_SUBJECT}),
        jwks_available=True,
    )
    token = mint_access_token(signing_material, kid=OTHER_KID)
    decision = _run_authorize(authorizer, token)
    assert decision.outcome is TrackingAuthorizationOutcome.UNAUTHENTICATED
    assert client.refresh_calls == 1


def test_jwks_unavailable_raises_503(
    signing_material,
) -> None:
    unavailable = FakeJwksClient(keys_by_kid={}, available=False)
    unavailable_verifier = JwtVerifier(
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        allowed_algorithms=("RS256",),
        jwks_client=unavailable,
    )
    authorizer = JwtQueryAuthorizer(
        verifier=unavailable_verifier,
        shipment_policy=AllowShipmentAccessPolicy(allowed_subjects={TEST_SUBJECT}),
        jwks_available=False,
    )
    token = mint_access_token(signing_material)
    with pytest.raises(AuthorizerUnavailableError):
        _run_authorize(authorizer, token)


def test_timeline_api_valid_token_allowed(
    store: MemoryTrackingStore,
    signing_material,
    jwks_client: FakeJwksClient,
    allow_policy: AllowShipmentAccessPolicy,
) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=jwks_client,
        shipment_policy=allow_policy,
        jwks_available=True,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    token = mint_access_token(signing_material)
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_timeline_api_valid_token_forbidden(
    store: MemoryTrackingStore,
    signing_material,
    jwks_client: FakeJwksClient,
) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=jwks_client,
        shipment_policy=DenyShipmentAccessPolicy(),
        jwks_available=True,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    token = mint_access_token(signing_material)
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_timeline_api_invalid_token_401(
    store: MemoryTrackingStore,
    jwks_client: FakeJwksClient,
    allow_policy: AllowShipmentAccessPolicy,
) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=jwks_client,
        shipment_policy=allow_policy,
        jwks_available=True,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401


def test_timeline_api_jwks_unavailable_503(
    store: MemoryTrackingStore,
    signing_material,
) -> None:
    unavailable = FakeJwksClient(keys_by_kid={}, available=False, fail_refresh=True)
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=unavailable,
        shipment_policy=AllowShipmentAccessPolicy(allowed_subjects={TEST_SUBJECT}),
        jwks_available=False,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    token = mint_access_token(signing_material)
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


def test_identity_headers_do_not_authorize_with_jwt_wiring(
    store: MemoryTrackingStore,
    jwks_client: FakeJwksClient,
    allow_policy: AllowShipmentAccessPolicy,
) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=jwks_client,
        shipment_policy=allow_policy,
        jwks_available=True,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={
            "X-User-Id": str(TEST_SUBJECT),
            "X-Role": "SUPER_ADMIN",
        },
    )
    assert response.status_code == 401


def test_token_absent_from_error_responses(
    store: MemoryTrackingStore,
    signing_material,
    jwks_client: FakeJwksClient,
    allow_policy: AllowShipmentAccessPolicy,
) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST, jwt=_jwt_settings())
    app = create_app(
        settings,
        query_port=store,
        jwks_client=jwks_client,
        shipment_policy=allow_policy,
        jwks_available=True,
    )
    app.state.timeline_query_service = TimelineQueryService(query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    token = mint_access_token(signing_material)
    token = f"{token}.{SENSITIVE_TOKEN_MARKER}"
    response = client.get(
        f"/tracking/shipments/{SHIPMENT_ID}/timeline",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert SENSITIVE_TOKEN_MARKER not in response.text
    assert "eyJ" not in response.text


def test_staging_requires_https_jwks_url() -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.STAGING,
        jwt=_jwt_settings(jwks_url="http://identity.example.test/jwks.json"),
        adr_0010_credentials_configured=True,
    )
    with pytest.raises(Exception, match="https"):
        settings.assert_query_auth_gates()


def test_production_jwt_readiness_distinguishes_components(
    signing_material,
    jwks_client: FakeJwksClient,
) -> None:
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        adr_0010_credentials_configured=True,
        jwt=_jwt_settings(),
    )
    default_deny_app = create_app(
        settings,
        query_port=MemoryTrackingStore(),
        jwks_client=jwks_client,
        jwks_available=True,
        nats_reachable=True,
        nats_binding_verified=True,
    )
    report = default_deny_app.state.readiness_report
    assert report.checks["jwt_verifier_configured"] is True
    assert report.checks["jwks_dependency_available"] is True
    assert report.checks["shipment_access_policy_configured"] is False
    assert report.checks["query_authorizer_configured"] is False
    assert "shipment_access_policy_not_configured" in report.blockers

    ready_app = create_app(
        settings,
        query_port=MemoryTrackingStore(),
        jwks_client=jwks_client,
        shipment_policy=AllowShipmentAccessPolicy(
            allowed_subjects={TEST_SUBJECT},
            production_ready=True,
        ),
        jwks_available=True,
        nats_reachable=True,
        nats_binding_verified=True,
    )
    ready_report = ready_app.state.readiness_report
    assert ready_report.checks["shipment_access_policy_configured"] is True
    assert ready_report.checks["query_authorizer_configured"] is True


def test_symmetric_algorithm_rejected_at_verifier_construction() -> None:
    client = FakeJwksClient(keys_by_kid={})
    with pytest.raises(ValueError, match="not permitted"):
        JwtVerifier(
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            allowed_algorithms=("HS256",),
            jwks_client=client,
        )


def test_none_algorithm_rejected_at_verifier_construction() -> None:
    client = FakeJwksClient(keys_by_kid={})
    with pytest.raises(ValueError, match="not permitted"):
        JwtVerifier(
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            allowed_algorithms=("none",),
            jwks_client=client,
        )
