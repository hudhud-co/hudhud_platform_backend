"""Authenticated timeline query HTTP adapter tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from conftest import SHIPMENT_ID, a1_event_id
from fastapi.testclient import TestClient

from tracking.application.coordinator import TimelineConsumerCoordinator
from tracking.application.query import TimelineQueryService
from tracking.config import JwtSettings, RuntimeEnvironment, load_settings
from tracking.domain.types import Delivery
from tracking.infrastructure.memory import MemoryTrackingStore
from tracking.main import create_app
from tracking.ports.query_authorizer import (
    AuthorizerUnavailableError,
    TrackingAccessDecision,
    TrackingQueryAuthorizer,
)

OTHER_SHIPMENT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SENSITIVE_TOKEN = "super-secret-bearer-token-do-not-leak"


class FakeQueryAuthorizer:
    def __init__(
        self,
        *,
        decision: TrackingAccessDecision = TrackingAccessDecision.allow(),
        unavailable: bool = False,
        production_ready: bool = True,
        jwt_verifier_configured: bool | None = None,
        jwks_dependency_available: bool | None = None,
        shipment_access_policy_configured: bool | None = None,
    ) -> None:
        self._decision = decision
        self._unavailable = unavailable
        self._production_ready = production_ready
        self._jwt_verifier_configured = (
            production_ready if jwt_verifier_configured is None else jwt_verifier_configured
        )
        self._jwks_dependency_available = (
            production_ready if jwks_dependency_available is None else jwks_dependency_available
        )
        self._shipment_access_policy_configured = (
            production_ready
            if shipment_access_policy_configured is None
            else shipment_access_policy_configured
        )
        self.seen_tokens: list[str] = []

    @property
    def is_production_ready(self) -> bool:
        return self._production_ready

    @property
    def jwt_verifier_configured(self) -> bool:
        return self._jwt_verifier_configured

    @property
    def jwks_dependency_available(self) -> bool:
        return self._jwks_dependency_available

    @property
    def shipment_access_policy_configured(self) -> bool:
        return self._shipment_access_policy_configured

    async def authorize_timeline_read(
        self,
        *,
        bearer_token: str,
        shipment_id: UUID,
    ) -> TrackingAccessDecision:
        self.seen_tokens.append(bearer_token)
        if self._unavailable:
            raise AuthorizerUnavailableError("authorizer unavailable")
        return self._decision


def _seed_timeline(
    coordinator: TimelineConsumerCoordinator,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
    *,
    count: int = 4,
) -> None:
    for index in range(count):
        source_pk = UUID(f"{index:08x}-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        event_id = a1_event_id(source_pk=source_pk)
        coordinator.handle(
            make_delivery(
                make_envelope(
                    source_pk=source_pk,
                    event_id=str(event_id),
                    payload=make_payload(
                        source_pk=source_pk,
                        occurred_at=f"2026-08-30T{10 + index:02d}:00:00.000Z",
                        new_status=f"STATUS_{index}",
                    ),
                )
            )
        )


def _client(
    store: MemoryTrackingStore,
    *,
    authorizer: TrackingQueryAuthorizer | None = None,
    environment: RuntimeEnvironment = RuntimeEnvironment.TEST,
    max_page_size: int = 100,
) -> TestClient:
    query_service = TimelineQueryService(query_port=store, max_page_size=max_page_size)
    settings = load_settings(environment=environment)
    app = create_app(
        settings,
        query_authorizer=authorizer or FakeQueryAuthorizer(),
        query_port=store,
    )
    # Override query service to use bounded page size for pagination tests
    app.state.timeline_query_service = query_service
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def timeline_client(
    store: MemoryTrackingStore,
    coordinator: TimelineConsumerCoordinator,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> TestClient:
    _seed_timeline(coordinator, make_delivery, make_envelope, make_payload)
    return _client(store, max_page_size=2)


def _timeline_url(shipment_id: UUID = SHIPMENT_ID) -> str:
    return f"/tracking/shipments/{shipment_id}/timeline"


def test_successful_authorized_timeline_response(
    store: MemoryTrackingStore,
    coordinator: TimelineConsumerCoordinator,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    _seed_timeline(coordinator, make_delivery, make_envelope, make_payload)
    client = _client(store)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": f"Bearer {SENSITIVE_TOKEN}"},
        params={"limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["shipment_id"] == str(SHIPMENT_ID)
    assert len(body["entries"]) == 4
    entry = body["entries"][0]
    assert set(entry.keys()) == {
        "event_id",
        "occurred_at",
        "legacy_event_type",
        "previous_status",
        "new_status",
    }
    assert "source_pk" not in body
    assert SENSITIVE_TOKEN not in response.text


def test_deterministic_pagination_and_next_cursor(timeline_client: TestClient) -> None:
    first = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"limit": 2},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["entries"]) == 2
    assert first_body["next_cursor"] is not None

    second = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["entries"]) == 2
    assert second_body["next_cursor"] is None

    first_ids = {entry["event_id"] for entry in first_body["entries"]}
    second_ids = {entry["event_id"] for entry in second_body["entries"]}
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 4


def test_malformed_cursor(timeline_client: TestClient) -> None:
    response = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"cursor": "not-a-valid-cursor"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid cursor"


def test_cursor_shipment_mismatch(timeline_client: TestClient) -> None:
    first = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"limit": 1},
    )
    cursor = first.json()["next_cursor"]
    response = timeline_client.get(
        _timeline_url(OTHER_SHIPMENT_ID),
        headers={"Authorization": "Bearer valid-token"},
        params={"cursor": cursor},
    )
    assert response.status_code == 422


def test_limit_bounds(
    store: MemoryTrackingStore,
    coordinator: TimelineConsumerCoordinator,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    _seed_timeline(coordinator, make_delivery, make_envelope, make_payload)
    client = _client(store)
    zero = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"limit": 0},
    )
    assert zero.status_code == 422

    capped = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
        params={"limit": 500},
    )
    assert capped.status_code == 200
    assert len(capped.json()["entries"]) == 4


def test_missing_bearer_token(timeline_client: TestClient) -> None:
    response = timeline_client.get(_timeline_url())
    assert response.status_code == 401


def test_malformed_bearer_header(timeline_client: TestClient) -> None:
    response = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Token not-bearer"},
    )
    assert response.status_code == 401


def test_invalid_token(store: MemoryTrackingStore) -> None:
    authorizer = FakeQueryAuthorizer(decision=TrackingAccessDecision.unauthenticated())
    client = _client(store, authorizer=authorizer)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer rejected-token"},
    )
    assert response.status_code == 401


def test_authenticated_but_forbidden(store: MemoryTrackingStore) -> None:
    authorizer = FakeQueryAuthorizer(decision=TrackingAccessDecision.forbidden())
    client = _client(store, authorizer=authorizer)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 403


def test_authorizer_unavailable(store: MemoryTrackingStore) -> None:
    authorizer = FakeQueryAuthorizer(unavailable=True)
    client = _client(store, authorizer=authorizer)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 503


def test_default_deny_composition(store: MemoryTrackingStore) -> None:
    settings = load_settings(environment=RuntimeEnvironment.TEST)
    app = create_app(settings, query_port=store)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer any-token"},
    )
    assert response.status_code == 401


def test_identity_headers_do_not_authorize(timeline_client: TestClient) -> None:
    response = timeline_client.get(
        _timeline_url(),
        headers={
            "X-User-Id": str(SHIPMENT_ID),
            "X-Role": "SUPER_ADMIN",
        },
    )
    assert response.status_code == 401


def test_response_excludes_internal_fields(timeline_client: TestClient) -> None:
    response = timeline_client.get(
        _timeline_url(),
        headers={"Authorization": "Bearer valid-token"},
    )
    forbidden = {
        "source_position",
        "source_system",
        "source_table",
        "source_pk",
        "bridge_mapper_version",
        "actor_id",
        "safe_metadata",
        "metadata",
        "jetstream_stream",
        "nats_msg_id",
        "received_at",
    }
    assert response.status_code == 200
    for entry in response.json()["entries"]:
        assert forbidden.isdisjoint(entry.keys())
    assert forbidden.isdisjoint(response.json().keys())


def test_token_absent_from_errors(store: MemoryTrackingStore) -> None:
    authorizer = FakeQueryAuthorizer(decision=TrackingAccessDecision.unauthenticated())
    client = _client(store, authorizer=authorizer)
    response = client.get(
        _timeline_url(),
        headers={"Authorization": f"Bearer {SENSITIVE_TOKEN}"},
    )
    assert response.status_code == 401
    assert SENSITIVE_TOKEN not in response.text


def test_readiness_requires_real_authorizer_configuration() -> None:
    jwt_settings = JwtSettings(
        issuer="https://identity.example",
        audience="tracking",
        jwks_url="https://identity.example/.well-known/jwks.json",
        allowed_algorithms=("RS256",),
    )
    settings = load_settings(
        environment=RuntimeEnvironment.PRODUCTION,
        adr_0010_credentials_configured=True,
        jwt=jwt_settings,
    )
    app = create_app(
        settings,
        query_port=MemoryTrackingStore(),
        jwks_available=False,
        nats_reachable=True,
        nats_binding_verified=True,
    )
    report = app.state.readiness_report
    assert report.ready is False
    assert "jwks_dependency_unavailable" in report.blockers
    assert report.checks["jwt_verifier_configured"] is True
    assert report.checks["jwks_dependency_available"] is False
    assert report.checks["shipment_access_policy_configured"] is False

    ready_authorizer = FakeQueryAuthorizer(production_ready=True)
    ready_app = create_app(
        settings,
        query_authorizer=ready_authorizer,
        query_port=MemoryTrackingStore(),
        nats_reachable=True,
        nats_binding_verified=True,
    )
    ready_report = ready_app.state.readiness_report
    assert ready_report.checks["query_authorizer_configured"] is True
    assert ready_report.checks["jwt_verifier_configured"] is True
    assert ready_report.checks["jwks_dependency_available"] is True
    assert ready_report.checks["shipment_access_policy_configured"] is True
