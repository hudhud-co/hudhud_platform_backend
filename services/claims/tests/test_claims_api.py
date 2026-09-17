"""Claims HTTP adapter: own-claim scoping, who decides, and what a driver never sees."""

from __future__ import annotations

from uuid import uuid4

import pytest
from claims_fixtures import next_tracking_code
from fastapi.testclient import TestClient

from claims.config import (
    ClaimsSettings,
    ProductionStartupBlockedError,
    RuntimeEnvironment,
)
from claims.infrastructure.authorizers.identity import FakeClaimsAuthorizer
from claims.infrastructure.memory import InMemoryUnitOfWork
from claims.main import create_app
from claims.ports.authorization import ClaimsActor, ClaimsRole

SENDER = uuid4()
RECEIVER = uuid4()
DRIVER = uuid4()
STAFF = uuid4()

SENDER_TOKEN = "sender-token"
RECEIVER_TOKEN = "receiver-token"
DRIVER_TOKEN = "driver-token"
SUPPORT_TOKEN = "support-token"
OPS_TOKEN = "ops-token"
ACCOUNTANT_TOKEN = "accountant-token"
#: An operator who also drives. SEC-07 is about what a *driver* is shown.
OPS_DRIVER_TOKEN = "ops-driver-token"


def build_client(**kwargs) -> tuple[TestClient, InMemoryUnitOfWork]:
    uow = InMemoryUnitOfWork()
    authorizer = FakeClaimsAuthorizer(
        token_actors={
            SENDER_TOKEN: ClaimsActor(
                principal_id=SENDER, roles=frozenset({ClaimsRole.CUSTOMER})
            ),
            RECEIVER_TOKEN: ClaimsActor(
                principal_id=RECEIVER, roles=frozenset({ClaimsRole.CUSTOMER})
            ),
            DRIVER_TOKEN: ClaimsActor(
                principal_id=DRIVER, roles=frozenset({ClaimsRole.LAST_MILE_DRIVER})
            ),
            SUPPORT_TOKEN: ClaimsActor(
                principal_id=STAFF, roles=frozenset({ClaimsRole.SUPPORT})
            ),
            OPS_TOKEN: ClaimsActor(
                principal_id=STAFF, roles=frozenset({ClaimsRole.OPERATIONS})
            ),
            ACCOUNTANT_TOKEN: ClaimsActor(
                principal_id=STAFF, roles=frozenset({ClaimsRole.ACCOUNTANT})
            ),
            OPS_DRIVER_TOKEN: ClaimsActor(
                principal_id=DRIVER,
                roles=frozenset(
                    {ClaimsRole.OPERATIONS, ClaimsRole.LAST_MILE_DRIVER}
                ),
            ),
        }
    )
    app = create_app(
        ClaimsSettings(environment=RuntimeEnvironment.TEST, **kwargs),
        unit_of_work=uow,
        authorizer=authorizer,
    )
    return TestClient(app), uow


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def file_claim(client: TestClient, token: str = SENDER_TOKEN, **overrides) -> str:
    body = {
        "tracking_code": next_tracking_code(),
        "kind": "LOST_PARCEL",
        "description": "Never arrived.",
    }
    body.update(overrides)
    response = client.post("/claims", json=body, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()["reference"]


def reviewed(client: TestClient, reference: str) -> None:
    """Take a claim to the point where it can be decided (CLM-03)."""
    response = client.post(
        f"/claims/{reference}/review",
        json={"custody_records_reviewed": True},
        headers=auth(OPS_TOKEN),
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401() -> None:
    client, _ = build_client()
    assert client.get("/claims").status_code == 401


def test_the_default_composition_denies_everything() -> None:
    app = create_app(
        ClaimsSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryUnitOfWork(),
    )
    assert TestClient(app).get("/claims", headers=auth(SENDER_TOKEN)).status_code == 401


def test_production_requires_identity_and_a_database() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(ClaimsSettings(environment=RuntimeEnvironment.PRODUCTION))
    message = str(caught.value)
    assert "CLAIMS_DATABASE_URL" in message
    assert "CLAIMS_IDENTITY_BASE_URL" in message


def test_production_starts_without_the_high_value_threshold() -> None:
    """CLM-08 is an Open Item, not a startup gate: everything else works without it."""
    app = create_app(
        ClaimsSettings(
            environment=RuntimeEnvironment.PRODUCTION,
            database_url="postgresql+psycopg://x/y",
            identity_base_url="http://identity",
            identity_service_credential="secret",
        ),
        unit_of_work=InMemoryUnitOfWork(),
    )
    assert app.state.high_value_policy.is_decided is False


def test_readiness_reports_the_undecided_high_value_threshold() -> None:
    client, _ = build_client()
    body = client.get("/ready").json()

    assert body["checks"]["high_value_threshold_decided"] is False
    assert "high_value_threshold_decided" in body["blockers"]


def test_a_decided_threshold_clears_that_blocker() -> None:
    client, _ = build_client(high_value_threshold_minor_units=1_000_000)
    body = client.get("/ready").json()

    assert body["checks"]["high_value_threshold_decided"] is True
    assert "high_value_threshold_decided" not in body.get("blockers", [])


# ------------------------------------------------------------------ CLM-02, 07


def test_a_sender_files_a_claim_and_gets_a_reference() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={
            "tracking_code": next_tracking_code(),
            "kind": "LOST_PARCEL",
            "description": "Never arrived.",
        },
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["reference"].startswith("CLM-")
    assert response.json()["status"] == "SUBMITTED"


def test_a_damage_claim_without_a_photograph_is_refused() -> None:
    """Customer App v3 `photosRequired` — refused at the boundary, not after review."""
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={"tracking_code": next_tracking_code(), "kind": "DAMAGED_IN_TRANSIT"},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "photographs_required"


def test_a_damage_claim_with_a_photograph_is_accepted() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={
            "tracking_code": next_tracking_code(),
            "kind": "DAMAGED_IN_TRANSIT",
            "evidence": [{"bucket": "claims-evidence", "key": "damage.jpg"}],
        },
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 201


def test_a_parcel_taken_inside_to_test_is_no_longer_hudhuds() -> None:
    """v6.3 p.37 — the liability ends there, and saying so now is kinder than later."""
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={
            "tracking_code": next_tracking_code(),
            "kind": "DAMAGED_IN_TRANSIT",
            "evidence": [{"bucket": "claims-evidence", "key": "damage.jpg"}],
            "custody_boundary": "TAKEN_INSIDE_TO_TEST",
        },
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "liability_ended_at_the_door"
    assert "inside" in response.json()["detail"]["reason"].lower()


def test_a_receiver_may_open_a_claim_and_the_sender_is_compensated() -> None:
    """CLM-02 opens it; CLM-01 pays the sender. Two different questions."""
    client, _ = build_client()
    tracking = next_tracking_code()

    reference = client.post(
        "/claims",
        json={
            "tracking_code": tracking,
            "kind": "WRONG_COD_AMOUNT",
            "sender_principal_id": str(SENDER),
        },
        headers=auth(RECEIVER_TOKEN),
    ).json()["reference"]

    # The sender sees it as theirs, because the money would go to them.
    assert client.get(f"/claims/{reference}", headers=auth(SENDER_TOKEN)).status_code == 200


def test_a_driver_may_open_a_claim() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={
            "tracking_code": next_tracking_code(),
            "kind": "LOST_PARCEL",
            "sender_principal_id": str(SENDER),
        },
        headers=auth(DRIVER_TOKEN),
    )

    assert response.status_code == 201


def test_who_opened_it_comes_from_the_token_not_the_body() -> None:
    client, _ = build_client()
    reference = file_claim(client, DRIVER_TOKEN, sender_principal_id=str(SENDER))

    staff = client.get("/claims/queue", headers=auth(OPS_TOKEN)).json()
    row = next(item for item in staff if item["reference"] == reference)

    assert row["opened_by"] == "DRIVER"


def test_a_second_open_claim_on_one_parcel_is_refused() -> None:
    client, _ = build_client()
    tracking = next_tracking_code()
    file_claim(client, tracking_code=tracking)

    response = client.post(
        "/claims",
        json={"tracking_code": tracking, "kind": "LOST_PARCEL"},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "claim_already_open_for_this_parcel"


def test_a_malformed_tracking_code_is_refused() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={"tracking_code": "not-a-parcel", "kind": "LOST_PARCEL"},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_tracking_code"


def test_an_invented_claim_kind_is_rejected_by_the_schema() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims",
        json={"tracking_code": next_tracking_code(), "kind": "CHANGED_MY_MIND"},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ own data


def test_the_list_shows_only_the_callers_own_claims() -> None:
    client, _ = build_client()
    file_claim(client, SENDER_TOKEN)

    mine = client.get("/claims", headers=auth(SENDER_TOKEN)).json()
    theirs = client.get("/claims", headers=auth(RECEIVER_TOKEN)).json()

    assert len(mine) == 1
    assert theirs == []


def test_another_persons_claim_answers_404_not_403() -> None:
    """A 403 would confirm the reference is real."""
    client, _ = build_client()
    reference = file_claim(client, SENDER_TOKEN)

    response = client.get(f"/claims/{reference}", headers=auth(RECEIVER_TOKEN))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "claim_not_found"


def test_a_claimant_withdraws_their_own_claim() -> None:
    client, _ = build_client()
    reference = file_claim(client, SENDER_TOKEN)

    response = client.post(
        f"/claims/{reference}/withdrawal", headers=auth(SENDER_TOKEN)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"


def test_a_stranger_cannot_withdraw_someone_elses_claim() -> None:
    client, _ = build_client()
    reference = file_claim(client, SENDER_TOKEN)

    response = client.post(
        f"/claims/{reference}/withdrawal", headers=auth(RECEIVER_TOKEN)
    )

    assert response.status_code == 404


# ------------------------------------------------------------------ CLM-06


def test_there_is_no_return_window_after_acceptance() -> None:
    """CLM-06 — a stated refusal with a reason, not a missing route."""
    client, _ = build_client()

    response = client.post(
        f"/claims/returns/{next_tracking_code()}", headers=auth(RECEIVER_TOKEN)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_return_window_after_acceptance"
    assert "claim" in response.json()["detail"]["reason"].lower()


def test_a_claim_is_still_possible_after_that_refusal() -> None:
    """No return window is not the same as no recourse."""
    client, _ = build_client()
    tracking = next_tracking_code()
    client.post(f"/claims/returns/{tracking}", headers=auth(RECEIVER_TOKEN))

    response = client.post(
        "/claims",
        json={"tracking_code": tracking, "kind": "WRONG_COD_AMOUNT"},
        headers=auth(RECEIVER_TOKEN),
    )

    assert response.status_code == 201


# ------------------------------------------------------------------ CLM-03, 04


def test_a_claim_cannot_be_approved_without_a_recorded_custody_review() -> None:
    """CLM-03 — "Hudhud reviews scan and custody records before compensating"."""
    client, _ = build_client()
    reference = file_claim(client)
    client.post(
        f"/claims/{reference}/review",
        json={"custody_records_reviewed": False},
        headers=auth(OPS_TOKEN),
    )

    response = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "custody_records_not_reviewed"


def test_a_submitted_claim_cannot_be_approved_straight_away() -> None:
    client, _ = build_client()
    reference = file_claim(client)

    response = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "claim_transition_not_allowed"


def test_operations_approves_and_the_amount_is_exact() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)

    response = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["compensation"] == {
        "minor_units": 450_000,
        "currency": "IQD",
    }


def test_an_accountant_may_also_approve() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)

    response = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 1},
        headers=auth(ACCOUNTANT_TOKEN),
    )

    assert response.status_code == 200


def test_support_may_review_but_may_not_approve() -> None:
    """CLM-04 — deliberately narrower than reviewing, and not widened for convenience."""
    client, _ = build_client()
    reference = file_claim(client)
    review = client.post(
        f"/claims/{reference}/review",
        json={"custody_records_reviewed": True},
        headers=auth(SUPPORT_TOKEN),
    )

    decide = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(SUPPORT_TOKEN),
    )

    assert review.status_code == 200
    assert decide.status_code == 403
    assert decide.json()["detail"]["code"] == "only_operations_decides_a_claim"


def test_a_customer_may_not_review_their_own_claim() -> None:
    client, _ = build_client()
    reference = file_claim(client)

    response = client.post(
        f"/claims/{reference}/review",
        json={"custody_records_reviewed": True},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 403


def test_a_rejection_always_carries_a_reason() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)

    response = client.post(
        f"/claims/{reference}/rejection",
        json={
            "reason": "CUSTODY_RECORD_DOES_NOT_SUPPORT_IT",
            "note": "Scanned at every hub and signed for.",
        },
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["rejection_reason"] == "CUSTODY_RECORD_DOES_NOT_SUPPORT_IT"


def test_a_free_text_rejection_reason_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)

    response = client.post(
        f"/claims/{reference}/rejection",
        json={"reason": "because we said so"},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 422


def test_an_amount_of_zero_is_refused_by_the_schema() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)

    response = client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 0},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 422


def test_the_claimant_sees_the_amount_only_once_approved() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)
    before = client.get(f"/claims/{reference}", headers=auth(SENDER_TOKEN)).json()
    client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )
    after = client.get(f"/claims/{reference}", headers=auth(SENDER_TOKEN)).json()

    assert before["compensation"] is None
    assert after["compensation"]["minor_units"] == 450_000


# ------------------------------------------------------------------ CLM-07


def test_a_claimant_and_support_talk_on_the_thread() -> None:
    client, _ = build_client()
    reference = file_claim(client)

    client.post(
        f"/claims/{reference}/messages",
        json={"body": "It never arrived."},
        headers=auth(SENDER_TOKEN),
    )
    client.post(
        f"/claims/{reference}/messages",
        json={"body": "We are checking the hub scans."},
        headers=auth(SUPPORT_TOKEN),
    )
    thread = client.get(
        f"/claims/{reference}/messages", headers=auth(SENDER_TOKEN)
    ).json()

    assert [message["author"] for message in thread["messages"]] == [
        "CLAIMANT",
        "SUPPORT",
    ]


def test_a_claimant_cannot_post_as_support() -> None:
    """The author comes from the token, so nothing a claimant sends looks official."""
    client, _ = build_client()
    reference = file_claim(client)

    client.post(
        f"/claims/{reference}/messages",
        json={"body": "HUDHUD has approved this."},
        headers=auth(SENDER_TOKEN),
    )
    thread = client.get(
        f"/claims/{reference}/messages", headers=auth(SENDER_TOKEN)
    ).json()

    assert thread["messages"][0]["author"] == "CLAIMANT"


def test_a_stranger_cannot_read_or_write_the_thread() -> None:
    client, _ = build_client()
    reference = file_claim(client, SENDER_TOKEN)

    read = client.get(f"/claims/{reference}/messages", headers=auth(RECEIVER_TOKEN))
    write = client.post(
        f"/claims/{reference}/messages",
        json={"body": "Any news?"},
        headers=auth(RECEIVER_TOKEN),
    )

    assert read.status_code == 404
    assert write.status_code == 404


def test_a_blank_message_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    reference = file_claim(client)

    response = client.post(
        f"/claims/{reference}/messages",
        json={"body": "   "},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 422


def test_a_decided_claim_takes_no_more_messages() -> None:
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)
    client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 1},
        headers=auth(OPS_TOKEN),
    )

    response = client.post(
        f"/claims/{reference}/messages",
        json={"body": "One more thing."},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "closed_claim_takes_no_messages"


# ------------------------------------------------------------------ DRV-P25


def test_a_driver_reports_an_incident_and_the_parcel_is_held() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims/incidents",
        json={
            "kind": "DAMAGE_AFTER_ACCEPTANCE",
            "tracking_code": next_tracking_code(),
            "note": "Corner crushed in the van.",
        },
        headers=auth(DRIVER_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["parcel_stays_in_custody"] is True


def test_a_vehicle_issue_needs_no_parcel() -> None:
    """Driver App v8 `needsParcel` — a breakdown is about the driver."""
    client, _ = build_client()

    response = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE", "note": "Flat tyre."},
        headers=auth(DRIVER_TOKEN),
    )

    assert response.status_code == 201
    assert response.json()["tracking_code"] is None
    assert response.json()["parcel_stays_in_custody"] is False


def test_a_parcel_incident_without_a_parcel_is_refused() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims/incidents",
        json={"kind": "PARCEL_MISSING"},
        headers=auth(DRIVER_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "incident_needs_a_parcel"


def test_only_a_driver_reports_an_incident() -> None:
    client, _ = build_client()

    response = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(SENDER_TOKEN),
    )

    assert response.status_code == 403


def test_a_driver_sees_their_own_reports() -> None:
    client, _ = build_client()
    client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    )

    mine = client.get("/claims/incidents", headers=auth(DRIVER_TOKEN)).json()

    assert len(mine) == 1


# ------------------------------------------------------------------ SEC-07


def test_no_driver_facing_response_has_a_field_for_an_amount() -> None:
    """SEC-07 — enforced by the shape, not by remembering to filter."""
    client, _ = build_client()
    reported = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    ).json()
    listed = client.get("/claims/incidents", headers=auth(DRIVER_TOKEN)).json()

    for body in (reported, *listed):
        assert "compensation" not in body
        assert "amount" not in body
        assert not any("minor_units" in key for key in body)


def test_the_openapi_driver_models_carry_no_amount() -> None:
    client, _ = build_client()
    schema = client.get("/openapi.json").json()["components"]["schemas"]

    for name in ("DriverSummaryResponse", "IncidentReportedResponse"):
        assert not any(
            "compensation" in field or "minor_units" in field
            for field in schema[name]["properties"]
        )


def test_an_operator_who_also_drives_sees_the_rows_without_the_amounts() -> None:
    """A driver who is also operations is still a driver — and still sees the view."""
    client, _ = build_client()
    reference = file_claim(client)
    reviewed(client, reference)
    client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )

    plain = client.get("/claims/returns-and-claims", headers=auth(OPS_TOKEN)).json()
    driving = client.get(
        "/claims/returns-and-claims", headers=auth(OPS_DRIVER_TOKEN)
    ).json()

    def row_for(body):
        return next(r for r in body["rows"] if r["reference"] == reference)

    assert row_for(plain)["compensation"] == {"minor_units": 450_000, "currency": "IQD"}
    assert row_for(driving)["compensation"] is None


def test_a_claimant_who_is_a_driver_is_not_shown_their_own_figure() -> None:
    client, _ = build_client()
    reference = file_claim(client, DRIVER_TOKEN, sender_principal_id=str(DRIVER))
    reviewed(client, reference)
    client.post(
        f"/claims/{reference}/approval",
        json={"compensation_minor_units": 450_000},
        headers=auth(OPS_TOKEN),
    )

    body = client.get(f"/claims/{reference}", headers=auth(DRIVER_TOKEN)).json()

    assert body["status"] == "APPROVED"
    assert body["compensation"] is None


# ------------------------------------------------------------------ OPS-06, 07


def test_operations_resolves_an_incident_and_says_what_was_found() -> None:
    client, _ = build_client()
    reference = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    ).json()["reference"]

    response = client.post(
        f"/claims/incidents/{reference}/resolution",
        json={"note": "Tyre replaced; driver back on the round."},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"


def test_support_may_not_resolve_a_driver_incident() -> None:
    """OPS-07 — operations only, and not widened."""
    client, _ = build_client()
    reference = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    ).json()["reference"]

    response = client.post(
        f"/claims/incidents/{reference}/resolution",
        json={"note": "Closed."},
        headers=auth(SUPPORT_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "only_operations_resolves_an_incident"


def test_a_resolution_without_a_note_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    reference = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    ).json()["reference"]

    response = client.post(
        f"/claims/incidents/{reference}/resolution",
        json={"note": ""},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 422


def test_the_held_parcels_view_lists_parcels_that_must_not_move() -> None:
    client, _ = build_client()
    tracking = next_tracking_code()
    client.post(
        "/claims/incidents",
        json={"kind": "PARCEL_MISSING", "tracking_code": tracking},
        headers=auth(DRIVER_TOKEN),
    )

    body = client.get("/claims/held-parcels", headers=auth(OPS_TOKEN)).json()

    assert tracking in body["tracking_codes"]


def test_a_resolved_incident_releases_the_parcel() -> None:
    client, _ = build_client()
    tracking = next_tracking_code()
    reference = client.post(
        "/claims/incidents",
        json={"kind": "PARCEL_MISSING", "tracking_code": tracking},
        headers=auth(DRIVER_TOKEN),
    ).json()["reference"]
    client.post(
        f"/claims/incidents/{reference}/resolution",
        json={"note": "Found at the hub."},
        headers=auth(OPS_TOKEN),
    )

    body = client.get("/claims/held-parcels", headers=auth(OPS_TOKEN)).json()

    assert tracking not in body["tracking_codes"]


def test_the_returns_and_claims_view_needs_support_or_operations() -> None:
    client, _ = build_client()

    denied = client.get("/claims/returns-and-claims", headers=auth(SENDER_TOKEN))
    allowed = client.get("/claims/returns-and-claims", headers=auth(SUPPORT_TOKEN))

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_the_returns_and_claims_view_carries_claims_and_incidents() -> None:
    client, _ = build_client()
    claim_reference = file_claim(client)
    incident_reference = client.post(
        "/claims/incidents",
        json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
        headers=auth(DRIVER_TOKEN),
    ).json()["reference"]

    rows = client.get(
        "/claims/returns-and-claims", headers=auth(OPS_TOKEN)
    ).json()["rows"]
    references = {row["reference"] for row in rows}

    assert claim_reference in references
    assert incident_reference in references


def test_a_claim_and_an_incident_never_share_a_reference() -> None:
    """Both are shown as ``CLM-…`` and OPS-06 lists them side by side."""
    client, _ = build_client()
    references = {file_claim(client) for _ in range(3)}
    references |= {
        client.post(
            "/claims/incidents",
            json={"kind": "VEHICLE_OR_SAFETY_ISSUE"},
            headers=auth(DRIVER_TOKEN),
        ).json()["reference"]
        for _ in range(3)
    }

    assert len(references) == 6


def test_the_queue_needs_support_or_operations() -> None:
    client, _ = build_client()
    file_claim(client)

    denied = client.get("/claims/queue", headers=auth(SENDER_TOKEN))
    allowed = client.get("/claims/queue", headers=auth(SUPPORT_TOKEN))

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_a_claim_reference_is_never_read_as_the_word_incidents() -> None:
    """Route ordering: `/claims/incidents` is the driver's list, not a claim."""
    client, _ = build_client()

    response = client.get("/claims/incidents", headers=auth(DRIVER_TOKEN))

    assert response.status_code == 200
    assert isinstance(response.json(), list)
