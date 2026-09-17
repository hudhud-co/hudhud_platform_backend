"""The HTTP adapter: authorization, the doorstep routes, and what never leaves.

The rule these tests are really about is that the driver acting on a stop is the
authenticated one. A body-supplied driver id would let one driver close another's stop,
so no route accepts one — which is asserted by there being nowhere to put it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from delivery_fixtures import TEST_CODE, TEST_CODE_LENGTH, TEST_HMAC_KEY
from fastapi.testclient import TestClient

from delivery.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    load_settings,
)
from delivery.infrastructure.authorizers.identity import (
    DefaultDenyDeliveryAuthorizer,
    FakeDeliveryAuthorizer,
)
from delivery.infrastructure.memory import InMemoryUnitOfWork
from delivery.main import create_app
from delivery.ports.authorization import (
    DeliveryActor,
    DeliveryCommand,
    DeliveryRole,
)

DRIVER_TOKEN = "driver-token"
OTHER_DRIVER_TOKEN = "other-driver-token"
OPERATIONS_TOKEN = "operations-token"
CUSTOMER_TOKEN = "customer-token"


@pytest.fixture
def driver_id() -> uuid4:
    return uuid4()


@pytest.fixture
def customer_id():
    return uuid4()


@pytest.fixture
def client(driver_id, customer_id) -> TestClient:
    actors = {
        DRIVER_TOKEN: DeliveryActor(
            principal_id=driver_id, roles=frozenset({DeliveryRole.LAST_MILE_DRIVER})
        ),
        OTHER_DRIVER_TOKEN: DeliveryActor(
            principal_id=uuid4(), roles=frozenset({DeliveryRole.LAST_MILE_DRIVER})
        ),
        OPERATIONS_TOKEN: DeliveryActor(
            principal_id=uuid4(), roles=frozenset({DeliveryRole.OPERATIONS})
        ),
        CUSTOMER_TOKEN: DeliveryActor(
            principal_id=customer_id, roles=frozenset({DeliveryRole.CUSTOMER})
        ),
    }
    settings = load_settings(
        environment=RuntimeEnvironment.TEST,
        delivery_code_length=TEST_CODE_LENGTH,
        delivery_code_hmac_key=TEST_HMAC_KEY,
    )
    app = create_app(
        settings,
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeDeliveryAuthorizer(token_actors=actors),
    )
    return TestClient(app)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def open_manifest(client: TestClient) -> str:
    response = client.post(
        "/delivery/manifests", json={"hub_id": str(uuid4())}, headers=bearer(DRIVER_TOKEN)
    )
    assert response.status_code == 201, response.text
    return response.json()["manifest_id"]


_CODES = {"n": 0}


def scan(client: TestClient, manifest_id: str, **overrides) -> dict:
    _CODES["n"] += 1
    body = {
        "tracking_code": f"SHP-20260915-{_CODES['n']:06d}",
        "delivery_code": TEST_CODE,
        **overrides,
    }
    response = client.post(
        f"/delivery/manifests/{manifest_id}/parcels",
        json=body,
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 201, response.text
    return response.json()


def to_the_door(client: TestClient, stop_id: str) -> None:
    assert (
        client.post(
            f"/delivery/stops/{stop_id}/depart",
            json={"eta_from_minutes": 20, "eta_to_minutes": 40},
            headers=bearer(DRIVER_TOKEN),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/delivery/stops/{stop_id}/arrive", headers=bearer(DRIVER_TOKEN)
        ).status_code
        == 200
    )


# ------------------------------------------------------------ authorization


def test_every_route_needs_a_bearer_token(client: TestClient) -> None:
    assert client.get("/delivery/stops").status_code == 401
    assert client.post("/delivery/manifests", json={"hub_id": str(uuid4())}).status_code == 401


def test_an_unknown_token_is_unauthenticated(client: TestClient) -> None:
    assert client.get("/delivery/stops", headers=bearer("nonsense")).status_code == 401


def test_a_service_with_no_identity_denies_everything() -> None:
    """Fail-closed composition: a delivery service that authorizes by default is worse."""
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=DefaultDenyDeliveryAuthorizer(),
    )
    client = TestClient(app)
    assert client.get("/delivery/stops", headers=bearer(DRIVER_TOKEN)).status_code == 401


def test_identity_being_unreachable_is_not_a_denial() -> None:
    """503, not 403: Identity failing says nothing about this caller."""
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeDeliveryAuthorizer(unavailable=True),
    )
    client = TestClient(app)
    response = client.get("/delivery/stops", headers=bearer(DRIVER_TOKEN))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "authorization_unavailable"


def test_a_denied_command_is_forbidden() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeDeliveryAuthorizer(
            token_actors={
                DRIVER_TOKEN: DeliveryActor(
                    principal_id=uuid4(),
                    roles=frozenset({DeliveryRole.LAST_MILE_DRIVER}),
                )
            },
            denied_commands=frozenset({DeliveryCommand.STOP_READ}),
        ),
    )
    client = TestClient(app)
    assert client.get("/delivery/stops", headers=bearer(DRIVER_TOKEN)).status_code == 403


def test_another_driver_cannot_read_this_stop(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    response = client.get(
        f"/delivery/stops/{stop['stop_id']}", headers=bearer(OTHER_DRIVER_TOKEN)
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "not_this_drivers_stop"


def test_another_driver_cannot_scan_onto_this_manifest(client: TestClient) -> None:
    manifest = open_manifest(client)
    response = client.post(
        f"/delivery/manifests/{manifest}/parcels",
        json={"tracking_code": "SHP-20260915-900001"},
        headers=bearer(OTHER_DRIVER_TOKEN),
    )
    assert response.status_code == 403


def test_no_route_accepts_a_driver_id_in_the_body(client: TestClient) -> None:
    """The acting driver is the authenticated one, always."""
    manifest = open_manifest(client)
    response = client.post(
        f"/delivery/manifests/{manifest}/parcels",
        json={
            "tracking_code": "SHP-20260915-900002",
            "driver_principal_id": str(uuid4()),
        },
        headers=bearer(DRIVER_TOKEN),
    )
    # `extra="forbid"` on every request model turns this into a 422, not a silent drop.
    assert response.status_code == 422


# ------------------------------------------------------------- the doorstep


def test_a_scan_puts_the_parcel_in_this_drivers_custody(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    assert stop["status"] == "IN_CUSTODY"
    assert stop["custody_taken_at"] is not None


def test_the_response_never_carries_the_code(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    assert stop["has_delivery_code"] is True
    assert TEST_CODE not in str(stop)
    assert "delivery_code_digest" not in stop


def test_the_door_sequence_runs_end_to_end(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    stop_id = stop["stop_id"]
    to_the_door(client, stop_id)

    verified = client.post(
        f"/delivery/stops/{stop_id}/verify/code",
        json={"code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["stop"]["status"] == "AUTHORIZED"

    inspected = client.post(
        f"/delivery/stops/{stop_id}/inspection",
        json={"outcome": "SEALED_ACCEPTED"},
        headers=bearer(DRIVER_TOKEN),
    )
    assert inspected.status_code == 200

    paid = client.post(
        f"/delivery/stops/{stop_id}/payment/prepaid",
        json={},
        headers=bearer(DRIVER_TOKEN),
    )
    assert paid.status_code == 200

    delivered = client.post(
        f"/delivery/stops/{stop_id}/deliver", headers=bearer(DRIVER_TOKEN)
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["stop"]["status"] == "DELIVERED"


def test_a_wrong_code_answers_422_and_says_nothing_about_the_real_one(
    client: TestClient,
) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/code",
        json={"code": "111111"},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "delivery_code_incorrect"}}
    assert TEST_CODE not in response.text


def test_the_attempt_limit_answers_429(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    for _ in range(5):
        client.post(
            f"/delivery/stops/{stop['stop_id']}/verify/code",
            json={"code": "111111"},
            headers=bearer(DRIVER_TOKEN),
        )
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/code",
        json={"code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 429


def test_the_id_fallback_is_refused_for_anyone_but_the_named_receiver(
    client: TestClient,
) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest, named_receiver="Zaid Al-Rawi")
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/id",
        json={"presented_name": "Someone Else"},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 403
    assert (
        response.json()["detail"]["code"] == "id_fallback_is_only_for_the_named_receiver"
    )


def test_the_id_fallback_accepts_the_named_receiver(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest, named_receiver="Zaid Al-Rawi")
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/id",
        json={"presented_name": "  zaid  al-rawi "},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 200
    assert response.json()["stop"]["status"] == "AUTHORIZED"


def test_the_id_route_has_no_field_for_a_photograph(client: TestClient) -> None:
    """DRV-L07 — there is nowhere to send one, so none can be retained."""
    manifest = open_manifest(client)
    stop = scan(client, manifest, named_receiver="Zaid Al-Rawi")
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/id",
        json={
            "presented_name": "Zaid Al-Rawi",
            "id_photo": {"bucket": "evidence", "key": "id.jpg"},
        },
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 422


def test_the_code_policy_route_reports_the_conflict(client: TestClient) -> None:
    response = client.get("/delivery/code-policy", headers=bearer(DRIVER_TOKEN))
    body = response.json()
    assert body["conflicting_lengths"] == [4, 6]
    assert "4" in body["conflict_sources"]
    assert "6" in body["conflict_sources"]


def test_an_undecided_code_length_answers_501_not_400() -> None:
    """The request is well formed and the service is healthy; the decision is missing."""
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.TEST,
            delivery_code_hmac_key=TEST_HMAC_KEY,
        ),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=FakeDeliveryAuthorizer(
            token_actors={
                DRIVER_TOKEN: DeliveryActor(
                    principal_id=uuid4(),
                    roles=frozenset({DeliveryRole.LAST_MILE_DRIVER}),
                )
            }
        ),
    )
    client = TestClient(app)
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/verify/code",
        json={"code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "delivery_code_length_not_decided"


# ------------------------------------------------------------------ payment


def test_a_cod_parcel_cannot_be_delivered_unpaid(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        cod_amount={"minor_units": 25000, "currency": "IQD"},
        payment_method_expected="CASH",
    )
    _authorize(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/deliver", headers=bearer(DRIVER_TOKEN)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "payment_required_before_handover"


def test_cash_is_reported_as_entering_driver_custody(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        cod_amount={"minor_units": 25000, "currency": "IQD"},
        payment_method_expected="CASH",
    )
    _authorize(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/payment/cash",
        json={},
        headers=bearer(DRIVER_TOKEN),
    )
    body = response.json()
    assert body["enters_driver_cash_custody"] is True
    assert body["amount"] == {"minor_units": 25000, "currency": "IQD"}


def test_a_card_payment_without_proof_answers_422(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        cod_amount={"minor_units": 25000, "currency": "IQD"},
        payment_method_expected="POS_CARD",
    )
    _authorize(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/payment/card/approved",
        json={},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "pos_proof_required"


def test_money_crosses_the_api_as_integer_minor_units(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        cod_amount={"minor_units": 25000, "currency": "IQD"},
        payment_method_expected="CASH",
    )
    assert isinstance(stop["cod_amount"]["minor_units"], int)


def test_a_fractional_amount_is_refused(client: TestClient) -> None:
    """Never a float for money: a fraction of a minor unit does not exist."""
    manifest = open_manifest(client)
    _CODES["n"] += 1
    response = client.post(
        f"/delivery/manifests/{manifest}/parcels",
        json={
            "tracking_code": f"SHP-20260915-{_CODES['n']:06d}",
            "cod_amount": {"minor_units": 25000.5, "currency": "IQD"},
            "payment_method_expected": "CASH",
        },
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ OPS-08


def test_a_driver_cannot_decide_the_next_attempt(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    failed = client.post(
        f"/delivery/stops/{stop['stop_id']}/verification-failed",
        headers=bearer(DRIVER_TOKEN),
    ).json()
    response = client.post(
        f"/delivery/failed-attempts/{failed['attempt']['attempt_id']}/next-attempt",
        json={"decision": "RETRY"},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 403
    assert (
        response.json()["detail"]["code"] == "only_operations_decides_the_next_attempt"
    )


def test_operations_decides_the_next_attempt(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    failed = client.post(
        f"/delivery/stops/{stop['stop_id']}/verification-failed",
        headers=bearer(DRIVER_TOKEN),
    ).json()
    response = client.post(
        f"/delivery/failed-attempts/{failed['attempt']['attempt_id']}/next-attempt",
        json={"decision": "HOLD_AT_HUB"},
        headers=bearer(OPERATIONS_TOKEN),
    )
    assert response.status_code == 200, response.text
    assert response.json()["awaits_operations"] is False


def test_a_driver_cannot_read_the_operations_queue(client: TestClient) -> None:
    assert (
        client.get("/delivery/failed-attempts", headers=bearer(DRIVER_TOKEN)).status_code
        == 403
    )


# ------------------------------------------------------------------ receiver


def test_a_receiver_can_set_a_handover_preference(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    response = client.put(
        f"/delivery/parcels/{stop['tracking_code']}/handover-preference",
        json={
            "window": {"starts_at_hour": 16, "ends_at_hour": 20},
            "landmark": "Second floor, green door",
        },
        headers=bearer(CUSTOMER_TOKEN),
    )
    assert response.status_code == 200, response.text
    assert response.json()["window"]["starts_at_hour"] == 16


def test_an_empty_preference_answers_422(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    response = client.put(
        f"/delivery/parcels/{stop['tracking_code']}/handover-preference",
        json={},
        headers=bearer(CUSTOMER_TOKEN),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "empty_handover_preference"


def test_the_arrival_view_carries_no_code_and_no_driver_contact(
    client: TestClient,
) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    body = client.get(
        f"/delivery/parcels/{stop['tracking_code']}/arrival",
        headers=bearer(CUSTOMER_TOKEN),
    ).json()
    assert body["wait_minutes_at_door"] == 10
    assert TEST_CODE not in str(body)
    assert "driver" not in str(body).lower()


def test_a_courier_rating_is_private(client: TestClient, driver_id) -> None:
    """SEC-08 — the courier sees the aggregate and neither the rater nor the note."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    _deliver(client, stop["stop_id"])
    rated = client.post(
        f"/delivery/parcels/{stop['tracking_code']}/rating",
        json={"score": 5, "tags": ["POLITE"], "note": "Secret note"},
        headers=bearer(CUSTOMER_TOKEN),
    )
    assert rated.status_code == 201, rated.text

    summary = client.get(
        f"/delivery/couriers/{driver_id}/rating-summary", headers=bearer(DRIVER_TOKEN)
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["rating_count"] == 1
    assert "Secret note" not in summary.text
    assert "rated_by_principal_id" not in body
    assert "note" not in body


def test_a_courier_cannot_read_another_couriers_ratings(client: TestClient) -> None:
    response = client.get(
        f"/delivery/couriers/{uuid4()}/rating-summary", headers=bearer(DRIVER_TOKEN)
    )
    assert response.status_code == 403


def test_an_issue_report_is_recorded(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    response = client.post(
        f"/delivery/parcels/{stop['tracking_code']}/issues",
        json={"kind": "DAMAGED", "detail": "The box arrived crushed"},
        headers=bearer(CUSTOMER_TOKEN),
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "DAMAGED"


# ------------------------------------------------------------------ readiness


def test_readiness_names_every_missing_gate() -> None:
    app = create_app(
        load_settings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
        authorizer=DefaultDenyDeliveryAuthorizer(),
    )
    body = TestClient(app).get("/ready").json()
    assert body["status"] == "not_ready"
    assert "delivery_code_length_decided" in body["blockers"]
    assert "workforce_eligibility_configured" in body["blockers"]


def test_production_refuses_to_start_without_its_gates() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(load_settings(environment=RuntimeEnvironment.PRODUCTION))
    message = str(caught.value)
    assert "DELIVERY_DATABASE_URL" in message
    assert "DELIVERY_WORKFORCE_BASE_URL" in message
    assert "DELIVERY_CODE_HMAC_KEY" in message


def test_production_starts_without_a_decided_code_length() -> None:
    """A business decision must not stop the service from booting.

    Everything except code verification works; verification answers 501 until it is made.
    """
    app = create_app(
        load_settings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql+psycopg://localhost/delivery",
            identity_base_url="http://identity",
            identity_service_credential="x",
            workforce_base_url="http://workforce",
            workforce_service_credential="y",
            delivery_code_hmac_key="z",
            delivery_code_length=None,
        ),
        unit_of_work=InMemoryUnitOfWork(),
    )
    assert app.state.settings.delivery_code_decided is False


def _authorize(client: TestClient, stop_id: str) -> None:
    to_the_door(client, stop_id)
    client.post(
        f"/delivery/stops/{stop_id}/verify/code",
        json={"code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )
    client.post(
        f"/delivery/stops/{stop_id}/inspection",
        json={"outcome": "SEALED_ACCEPTED"},
        headers=bearer(DRIVER_TOKEN),
    )


def _deliver(client: TestClient, stop_id: str) -> None:
    _authorize(client, stop_id)
    client.post(
        f"/delivery/stops/{stop_id}/payment/prepaid",
        json={},
        headers=bearer(DRIVER_TOKEN),
    )
    client.post(f"/delivery/stops/{stop_id}/deliver", headers=bearer(DRIVER_TOKEN))


# ------------------------------------------------------- DRV-L20 and DRV-L21


def test_the_operations_queue_reports_when_the_hold_expires(client: TestClient) -> None:
    """DRV-L20 — v6.3 p.29's three days, visible to whoever has to act on them."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    client.post(
        f"/delivery/stops/{stop['stop_id']}/verification-failed",
        headers=bearer(DRIVER_TOKEN),
    )
    queue = client.get(
        "/delivery/failed-attempts", headers=bearer(OPERATIONS_TOKEN)
    ).json()
    assert queue[0]["hold_expires_at"] is not None


def test_nothing_is_past_its_hold_on_the_day_it_failed(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    client.post(
        f"/delivery/stops/{stop['stop_id']}/verification-failed",
        headers=bearer(DRIVER_TOKEN),
    )
    response = client.get(
        "/delivery/failed-attempts/past-hold", headers=bearer(OPERATIONS_TOKEN)
    )
    assert response.status_code == 200
    assert response.json() == []


def test_the_return_sweep_reports_the_hold_it_applied(client: TestClient) -> None:
    response = client.post(
        "/delivery/failed-attempts/return-past-hold", headers=bearer(OPERATIONS_TOKEN)
    )
    assert response.status_code == 200, response.text
    assert response.json()["hold_days"] == 3


def test_a_driver_cannot_run_the_return_sweep(client: TestClient) -> None:
    assert (
        client.post(
            "/delivery/failed-attempts/return-past-hold", headers=bearer(DRIVER_TOKEN)
        ).status_code
        == 403
    )


def test_a_driver_opens_an_incident_from_the_stop(client: TestClient) -> None:
    """DRV-L21 — Driver App v8 `lmIncident`."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/incident",
        json={"kind": "DAMAGED_IN_CUSTODY", "detail": "Dropped while unloading"},
        headers=bearer(DRIVER_TOKEN),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "DRIVER"
    assert body["stop_id"] == stop["stop_id"]


def test_another_driver_cannot_open_an_incident_on_this_stop(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    response = client.post(
        f"/delivery/stops/{stop['stop_id']}/incident",
        json={"kind": "LOST_IN_CUSTODY"},
        headers=bearer(OTHER_DRIVER_TOKEN),
    )
    assert response.status_code == 403


def test_a_receiver_report_is_marked_as_the_receivers(client: TestClient) -> None:
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    body = client.post(
        f"/delivery/parcels/{stop['tracking_code']}/issues",
        json={"kind": "DAMAGED"},
        headers=bearer(CUSTOMER_TOKEN),
    ).json()
    assert body["source"] == "RECEIVER"
    assert body["stop_id"] is None


# ------------------------------------------------- the ten minutes at the door


def test_the_wait_countdown_survives_reading_the_stop_again(client: TestClient) -> None:
    """v6.3 p.26 — the Driver App's ten-minute timer is the server's clock.

    `POST /wait` answered with the remaining seconds, but reading the stop back
    answered `null`, so a driver who backgrounded the app or reopened it had no
    countdown left to show — and the only way to draw one would have been to run
    a clock on the device, which is exactly what makes the wait shortenable.
    """
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])

    started = client.post(
        f"/delivery/stops/{stop['stop_id']}/wait", headers=bearer(DRIVER_TOKEN)
    )
    assert started.status_code == 200, started.text
    assert started.json()["wait_remaining_seconds"] is not None

    read_again = client.get(
        f"/delivery/stops/{stop['stop_id']}", headers=bearer(DRIVER_TOKEN)
    )
    assert read_again.status_code == 200, read_again.text
    body = read_again.json()
    assert body["status"] == "WAITING"
    assert body["wait_remaining_seconds"] is not None, (
        "reading the stop lost the countdown"
    )
    assert 0 < body["wait_remaining_seconds"] <= 600


def test_the_stop_list_carries_the_countdown_too(client: TestClient) -> None:
    """The work list shows a waiting stop, so it needs the same number."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)
    to_the_door(client, stop["stop_id"])
    client.post(f"/delivery/stops/{stop['stop_id']}/wait", headers=bearer(DRIVER_TOKEN))

    listed = client.get("/delivery/stops", headers=bearer(DRIVER_TOKEN))
    assert listed.status_code == 200, listed.text
    waiting = [row for row in listed.json() if row["stop_id"] == stop["stop_id"]]
    assert waiting, "the waiting stop is missing from the driver's list"
    assert waiting[0]["wait_remaining_seconds"] is not None


def test_a_stop_that_is_not_waiting_reports_no_countdown(client: TestClient) -> None:
    """Only a started wait has a remaining time; nothing is invented for the rest."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)

    read = client.get(f"/delivery/stops/{stop['stop_id']}", headers=bearer(DRIVER_TOKEN))
    assert read.status_code == 200, read.text
    assert read.json()["wait_remaining_seconds"] is None


# ------------------------------------------------- what was already collected


def test_a_stop_says_whether_money_was_already_taken(client: TestClient) -> None:
    """v6.3 p.31 — a driver must not ask a receiver for the same cash twice.

    The service already refuses a second *recording*, so the ledger was never at
    risk. What was missing is upstream of that: reading a stop said nothing about
    payment, so a client that restarted between collecting the cash and
    completing the delivery had no way to know the money was in its hand
    already, and would show the payment step again and ask for it a second time.

    The harm is physical, not accounting, which is why the answer has to be on
    the stop the driver re-reads rather than only in the command's response.
    """
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        payment_method_expected="CASH",
        cod_amount={"minor_units": 45000, "currency": "IQD"},
    )
    stop_id = stop["stop_id"]
    to_the_door(client, stop_id)

    before = client.get(f"/delivery/stops/{stop_id}", headers=bearer(DRIVER_TOKEN))
    assert before.status_code == 200, before.text
    assert before.json()["payment"] is None, "nothing has been collected yet"

    collected = client.post(
        f"/delivery/stops/{stop_id}/payment/cash", json={}, headers=bearer(DRIVER_TOKEN)
    )
    assert collected.status_code == 200, collected.text

    after = client.get(f"/delivery/stops/{stop_id}", headers=bearer(DRIVER_TOKEN))
    assert after.status_code == 200, after.text
    payment = after.json()["payment"]
    assert payment is not None, "the stop must say the cash was taken"
    assert payment["outcome"] == "COLLECTED"
    assert payment["method"] == "CASH"
    assert payment["enters_driver_cash_custody"] is True
    assert payment["amount"]["minor_units"] == 45000


def test_a_declined_card_leaves_the_stop_still_payable(client: TestClient) -> None:
    """A decline took no money, so the stop must not read as paid.

    `record_card_decline` deliberately does not fill the one payment slot, so the
    fallback to cash can still fill it. The stop therefore answers `payment:
    null` — which is the truthful answer to "has money been taken here?" and is
    what lets a re-opened app offer cash rather than assume the card worked.
    """
    manifest = open_manifest(client)
    stop = scan(
        client,
        manifest,
        payment_method_expected="POS_CARD",
        cod_amount={"minor_units": 78500, "currency": "IQD"},
    )
    stop_id = stop["stop_id"]
    to_the_door(client, stop_id)

    declined = client.post(
        f"/delivery/stops/{stop_id}/payment/card/declined",
        json={},
        headers=bearer(DRIVER_TOKEN),
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["outcome"] == "DECLINED"
    assert declined.json()["enters_driver_cash_custody"] is False

    read = client.get(f"/delivery/stops/{stop_id}", headers=bearer(DRIVER_TOKEN))
    assert read.json()["payment"] is None, "a decline is not a collection"

    # And the cash fallback still works, filling the slot for real.
    cash = client.post(
        f"/delivery/stops/{stop_id}/payment/cash", json={}, headers=bearer(DRIVER_TOKEN)
    )
    assert cash.status_code == 200, cash.text
    after = client.get(f"/delivery/stops/{stop_id}", headers=bearer(DRIVER_TOKEN))
    assert after.json()["payment"]["outcome"] == "COLLECTED"
    assert after.json()["payment"]["method"] == "CASH"


def test_the_stop_never_carries_the_delivery_code_alongside_the_payment(
    client: TestClient,
) -> None:
    """Adding a field must not widen what a stop discloses."""
    manifest = open_manifest(client)
    stop = scan(client, manifest, payment_method_expected="CASH",
                cod_amount={"minor_units": 1000, "currency": "IQD"})
    to_the_door(client, stop["stop_id"])
    client.post(
        f"/delivery/stops/{stop['stop_id']}/payment/cash",
        json={}, headers=bearer(DRIVER_TOKEN),
    )

    body = client.get(
        f"/delivery/stops/{stop['stop_id']}", headers=bearer(DRIVER_TOKEN)
    ).text

    assert TEST_CODE not in body
    # `has_delivery_code` is the only field whose name contains "delivery_code",
    # so the check is for the key itself rather than the substring.
    assert '"delivery_code"' not in body
    assert '"has_delivery_code":true' in body.replace(" ", "")


# ------------------------------------------------- the shape of a tracking code


def test_a_tracking_code_the_contracts_reject_is_refused_at_the_scan(
    client: TestClient,
) -> None:
    """Refuse it where it enters, not three steps later as a 500.

    Nine event payload schemas fix the shape as `^SHP-\\d{8}-\\d{6}$`. The scan
    accepted any string up to 32 characters, so a parcel with a seven-digit
    serial went onto a manifest happily and then took custody — and the failure
    only appeared at `POST /depart`, as an envelope validation error the driver
    saw as a 500 on an unrelated step, with the parcel already in their van.
    """
    manifest = open_manifest(client)

    refused = client.post(
        f"/delivery/manifests/{manifest}/parcels",
        json={"tracking_code": "SHP-20260915-1234567", "delivery_code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )

    assert refused.status_code == 422, refused.text
    assert "tracking_code" in refused.text


def test_a_conformant_tracking_code_still_scans(client: TestClient) -> None:
    manifest = open_manifest(client)

    accepted = client.post(
        f"/delivery/manifests/{manifest}/parcels",
        json={"tracking_code": "SHP-20260915-123456", "delivery_code": TEST_CODE},
        headers=bearer(DRIVER_TOKEN),
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["tracking_code"] == "SHP-20260915-123456"


def test_the_departure_a_scanned_parcel_can_reach_is_publishable(
    client: TestClient,
) -> None:
    """The whole point: what the scan accepts, the facts can carry."""
    manifest = open_manifest(client)
    stop = scan(client, manifest)

    departed = client.post(
        f"/delivery/stops/{stop['stop_id']}/depart",
        json={"eta_from_minutes": 20, "eta_to_minutes": 40},
        headers=bearer(DRIVER_TOKEN),
    )

    assert departed.status_code == 200, departed.text
