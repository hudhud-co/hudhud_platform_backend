"""Hub HTTP adapter: posting, staff-only actions and the cut-off override."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from hub_fixtures import CUSTOMER, LINEHAUL_DRIVER, OPERATIONS, OPERATOR, TRACKING

from hub.config import HubSettings, ProductionStartupBlockedError, RuntimeEnvironment
from hub.infrastructure.authorizers.identity import FakeHubAuthorizer
from hub.infrastructure.memory import InMemoryHubUnitOfWork
from hub.main import create_app
from hub.ports.authorization import HubActor, HubRole

OPERATOR_TOKEN = "operator-token"
OTHER_OPERATOR_TOKEN = "other-operator-token"
OPS_TOKEN = "ops-token"
CUSTOMER_TOKEN = "customer-token"
DRIVER_TOKEN = "driver-token"


def build_client() -> tuple[TestClient, InMemoryHubUnitOfWork, dict]:
    uow = InMemoryHubUnitOfWork()
    app = create_app(
        HubSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=uow,
        authorizer=FakeHubAuthorizer(token_actors={}),
    )
    client = TestClient(app)
    # Register two hubs as Operations, then post an operator to only the first.
    ops_actor = HubActor(principal_id=OPERATIONS, roles=frozenset({HubRole.OPERATIONS}))
    app.state.authorizer = FakeHubAuthorizer(token_actors={OPS_TOKEN: ops_actor})
    karbala = client.post(
        "/hub/hubs",
        json={
            "code": "KRB",
            "name": "Karbala Hub",
            "governorate": "KARBALA",
            "cut_off_local_time": "18:00:00",
        },
        headers=auth(OPS_TOKEN),
    ).json()
    baghdad = client.post(
        "/hub/hubs",
        json={
            "code": "BGW",
            "name": "Baghdad Hub",
            "governorate": "BAGHDAD",
            "cut_off_local_time": "20:30:00",
        },
        headers=auth(OPS_TOKEN),
    ).json()
    app.state.authorizer = FakeHubAuthorizer(
        token_actors={
            OPS_TOKEN: ops_actor,
            OPERATOR_TOKEN: HubActor(
                principal_id=OPERATOR,
                roles=frozenset({HubRole.HUB_OPERATOR}),
                hub_ids=frozenset({_id(karbala)}),
            ),
            OTHER_OPERATOR_TOKEN: HubActor(
                principal_id=OPERATOR,
                roles=frozenset({HubRole.HUB_OPERATOR}),
                hub_ids=frozenset({_id(baghdad)}),
            ),
            CUSTOMER_TOKEN: HubActor(principal_id=CUSTOMER, roles=frozenset()),
            DRIVER_TOKEN: HubActor(
                principal_id=LINEHAUL_DRIVER,
                roles=frozenset({HubRole.LINEHAUL_DRIVER}),
            ),
        }
    )
    return client, uow, {"karbala": karbala, "baghdad": baghdad}


def _id(hub: dict) -> UUID:
    return UUID(hub["hub_id"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def a_drop_off(client: TestClient, hub: dict) -> dict:
    response = client.post(
        f"/hub/hubs/{hub['hub_id']}/drop-offs/details",
        json={
            "tracking_code": TRACKING,
            "details": {
                "receiver_phone": "+9647701234567",
                "destination_governorate": "BAGHDAD",
            },
        },
        headers=auth(OPERATOR_TOKEN),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401() -> None:
    client, _, hubs = build_client()
    response = client.post(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "BAGHDAD"},
    )
    assert response.status_code == 401


def test_the_default_composition_denies_everything() -> None:
    app = create_app(
        HubSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryHubUnitOfWork(),
    )
    client = TestClient(app)
    assert client.get("/hub/hubs", headers=auth(OPS_TOKEN)).status_code == 401


def test_production_refuses_to_start_without_identity() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            HubSettings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://x/y",
            )
        )
    assert "HUB_IDENTITY_BASE_URL" in str(caught.value)


# ------------------------------------------------------------------ posting


def test_an_operator_cannot_work_a_hub_they_are_not_posted_to() -> None:
    client, _, hubs = build_client()

    response = client.post(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "BAGHDAD"},
        headers=auth(OTHER_OPERATOR_TOKEN),
    )

    assert response.status_code == 403


def test_an_operator_can_work_their_own_hub() -> None:
    client, _, hubs = build_client()

    response = client.post(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "BAGHDAD"},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 201


def test_operations_can_work_any_hub() -> None:
    client, _, hubs = build_client()

    response = client.post(
        f"/hub/hubs/{hubs['baghdad']['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "KARBALA"},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 201


def test_only_operations_registers_a_hub() -> None:
    client, _, _ = build_client()

    response = client.post(
        "/hub/hubs",
        json={
            "code": "NJF",
            "name": "Najaf Hub",
            "governorate": "NAJAF",
            "cut_off_local_time": "19:00:00",
        },
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 403


# ------------------------------------------------------------------ CUS-05


def test_someone_who_is_not_hub_staff_cannot_label_a_parcel() -> None:
    """v6.3 p.18 — the actor comes from the token, so this cannot be spoofed."""
    client, _, hubs = build_client()
    drop_off = a_drop_off(client, hubs["karbala"])

    response = client.post(
        f"/hub/drop-offs/{drop_off['drop_off_id']}/label",
        json={"label_code": "HH-000001", "weight_grams": 1400},
        headers=auth(CUSTOMER_TOKEN),
    )

    assert response.status_code == 403


def test_hub_staff_label_weigh_and_accept() -> None:
    client, _, hubs = build_client()
    drop_off = a_drop_off(client, hubs["karbala"])

    labelled = client.post(
        f"/hub/drop-offs/{drop_off['drop_off_id']}/label",
        json={"label_code": "HH-000001", "weight_grams": 1400},
        headers=auth(OPERATOR_TOKEN),
    )
    accepted = client.post(
        f"/hub/drop-offs/{drop_off['drop_off_id']}/accept",
        json={"destination_governorate": "BAGHDAD"},
        headers=auth(OPERATOR_TOKEN),
    )

    assert labelled.json()["status"] == "LABELLED"
    assert labelled.json()["weight_grams"] == 1400
    assert accepted.json()["status"] == "ACCEPTED"


def test_a_zero_weight_is_rejected_by_the_schema() -> None:
    client, _, hubs = build_client()
    drop_off = a_drop_off(client, hubs["karbala"])

    response = client.post(
        f"/hub/drop-offs/{drop_off['drop_off_id']}/label",
        json={"label_code": "HH-000001", "weight_grams": 0},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 422


def test_details_taken_at_the_counter_must_be_complete() -> None:
    client, _, hubs = build_client()

    response = client.post(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/drop-offs/details",
        json={"tracking_code": TRACKING, "details": {}},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 422
    assert set(response.json()["detail"]["missing"]) == {
        "receiver_phone",
        "destination_governorate",
    }


def test_accepting_an_unlabelled_parcel_is_refused() -> None:
    client, _, hubs = build_client()
    drop_off = a_drop_off(client, hubs["karbala"])

    response = client.post(
        f"/hub/drop-offs/{drop_off['drop_off_id']}/accept",
        json={},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "drop_off_not_labelled"


# ------------------------------------------------------------------ SHP-07


def _consignment(client: TestClient, hubs: dict) -> str:
    karbala, baghdad = hubs["karbala"], hubs["baghdad"]
    client.post(
        f"/hub/hubs/{karbala['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "BAGHDAD"},
        headers=auth(OPERATOR_TOKEN),
    )
    client.post(
        f"/hub/hubs/{karbala['hub_id']}/parcels/sort",
        json={"tracking_code": TRACKING},
        headers=auth(OPERATOR_TOKEN),
    )
    consignment = client.post(
        f"/hub/hubs/{karbala['hub_id']}/consignments",
        json={"destination_hub_id": baghdad["hub_id"]},
        headers=auth(OPERATOR_TOKEN),
    ).json()
    client.post(
        f"/hub/consignments/{consignment['consignment_id']}/parcels",
        json={"tracking_code": TRACKING},
        headers=auth(OPERATOR_TOKEN),
    )
    return consignment["consignment_id"]


def test_a_consignment_cannot_be_dispatched_before_its_hubs_cut_off() -> None:
    """Pinned to 23:59 so the assertion does not depend on when the suite runs."""
    client, _, hubs = build_client()
    client.patch(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/cut-off",
        json={"cut_off_local_time": "23:59:00"},
        headers=auth(OPS_TOKEN),
    )
    consignment_id = _consignment(client, hubs)

    response = client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": False},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cut_off_not_reached"
    assert response.json()["detail"]["cut_off"] == "23:59"


def test_a_consignment_dispatches_once_its_hubs_cut_off_has_passed() -> None:
    client, _, hubs = build_client()
    client.patch(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/cut-off",
        json={"cut_off_local_time": "00:00:00"},
        headers=auth(OPS_TOKEN),
    )
    consignment_id = _consignment(client, hubs)

    response = client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": False},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "DISPATCHED"


def test_only_operations_may_override_a_cut_off() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)

    response = client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": True},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "cut_off_override_needs_operations"


def test_operations_can_override_a_cut_off() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)

    response = client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": True},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "DISPATCHED"


# ------------------------------------------------------------------ SHP-08


def test_a_seal_mismatch_reports_an_open_investigation() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)
    client.post(
        f"/hub/consignments/{consignment_id}/seal",
        json={"seal_code": "SEAL-0001"},
        headers=auth(OPERATOR_TOKEN),
    )
    client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": True},
        headers=auth(OPS_TOKEN),
    )
    client.post(
        f"/hub/consignments/{consignment_id}/arrival", headers=auth(OPS_TOKEN)
    )

    response = client.post(
        f"/hub/consignments/{consignment_id}/seal-check",
        json={"observed_seal_code": "SEAL-9999"},
        headers=auth(OPS_TOKEN),
    )

    body = response.json()
    assert body["outcome"] == "MISMATCHED"
    assert body["opened_investigation"] is True
    assert body["consignment"]["status"] == "UNDER_TAMPER_INVESTIGATION"


def test_a_tampered_consignment_cannot_be_reconciled_over_http() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)
    client.post(
        f"/hub/consignments/{consignment_id}/seal",
        json={"seal_code": "SEAL-0001"},
        headers=auth(OPERATOR_TOKEN),
    )
    client.post(
        f"/hub/consignments/{consignment_id}/dispatch",
        json={"override_cut_off": True},
        headers=auth(OPS_TOKEN),
    )
    client.post(f"/hub/consignments/{consignment_id}/arrival", headers=auth(OPS_TOKEN))
    client.post(
        f"/hub/consignments/{consignment_id}/seal-check",
        json={"observed_seal_code": None},
        headers=auth(OPS_TOKEN),
    )

    response = client.post(
        f"/hub/consignments/{consignment_id}/reconcile", headers=auth(OPS_TOKEN)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "seal_mismatch_requires_investigation"


# ------------------------------------------------------------------ SHP-13


def test_a_checkpoint_interception_returns_the_parcel_over_http() -> None:
    client, _, hubs = build_client()
    karbala = hubs["karbala"]
    client.post(
        f"/hub/hubs/{karbala['hub_id']}/parcels/scan-in",
        json={"tracking_code": TRACKING, "destination_governorate": "BAGHDAD"},
        headers=auth(OPERATOR_TOKEN),
    )

    response = client.post(
        f"/hub/hubs/{karbala['hub_id']}/parcels/hold",
        json={"tracking_code": TRACKING, "reason": "CHECKPOINT_INTERCEPTION"},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["disposition"] == "RETURN_TO_MERCHANT"


def test_an_invented_hold_reason_is_rejected_by_the_schema() -> None:
    client, _, hubs = build_client()
    karbala = hubs["karbala"]

    response = client.post(
        f"/hub/hubs/{karbala['hub_id']}/parcels/hold",
        json={"tracking_code": TRACKING, "reason": "BECAUSE"},
        headers=auth(OPERATOR_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ OPS


def test_the_activity_view_is_readable_by_posted_staff() -> None:
    client, _, hubs = build_client()

    response = client.get(
        f"/hub/hubs/{hubs['karbala']['hub_id']}/activity", headers=auth(OPERATOR_TOKEN)
    )

    assert response.status_code == 200
    assert set(response.json()) >= {"backlog", "held", "ready_for_last_mile"}


def test_the_activity_view_of_another_hub_is_refused() -> None:
    client, _, hubs = build_client()

    response = client.get(
        f"/hub/hubs/{hubs['baghdad']['hub_id']}/activity", headers=auth(OPERATOR_TOKEN)
    )

    assert response.status_code == 403


def test_both_position_sources_are_accepted_over_http() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)
    linehaul = client.post(
        f"/hub/consignments/{consignment_id}/linehaul",
        json={
            "vehicle_reference": "VAN-07",
            "driver_principal_id": str(LINEHAUL_DRIVER),
        },
        headers=auth(OPERATOR_TOKEN),
    ).json()

    for source in ("VEHICLE_TRACKER", "DRIVER_DEVICE"):
        response = client.post(
            f"/hub/linehauls/{linehaul['linehaul_id']}/positions",
            json={"source": source, "latitude": "32.6", "longitude": "44.0"},
            headers=auth(DRIVER_TOKEN),
        )
        assert response.status_code == 201

    positions = client.get(
        f"/hub/linehauls/{linehaul['linehaul_id']}/positions", headers=auth(OPS_TOKEN)
    ).json()
    assert {item["source"] for item in positions} == {"VEHICLE_TRACKER", "DRIVER_DEVICE"}


def test_an_invented_position_source_is_rejected() -> None:
    client, _, hubs = build_client()
    consignment_id = _consignment(client, hubs)
    linehaul = client.post(
        f"/hub/consignments/{consignment_id}/linehaul",
        json={
            "vehicle_reference": "VAN-07",
            "driver_principal_id": str(LINEHAUL_DRIVER),
        },
        headers=auth(OPERATOR_TOKEN),
    ).json()

    response = client.post(
        f"/hub/linehauls/{linehaul['linehaul_id']}/positions",
        json={"source": "GUESSWORK", "latitude": "32.6", "longitude": "44.0"},
        headers=auth(DRIVER_TOKEN),
    )

    assert response.status_code == 422


def test_there_is_no_route_that_lets_a_driver_split_a_batch() -> None:
    """v6.3 p.24 Role boundary — the absence is the requirement."""
    client, _, _ = build_client()
    paths = client.get("/openapi.json").json()["paths"]

    assert not [path for path in paths if "split" in path.lower()]
