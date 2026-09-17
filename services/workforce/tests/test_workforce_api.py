"""Workforce HTTP adapter.

Most of these tests are about OPS-09: a driver may ask, and support decides. The rest are
about SEC-03 — that no route makes someone assignable without the office check.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from workforce_fixtures import APPLICANT, OPERATOR, SUPPORT

from workforce.config import (
    ProductionStartupBlockedError,
    RuntimeEnvironment,
    WorkforceSettings,
)
from workforce.domain.value_objects import REQUIRED_OFFICE_DOCUMENTS
from workforce.infrastructure.authorizers.identity import FakeWorkforceAuthorizer
from workforce.infrastructure.memory import InMemoryWorkforceUnitOfWork
from workforce.main import create_app
from workforce.ports.authorization import WorkforceActor, WorkforceRole

APPLICANT_TOKEN = "applicant-token"
OPS_TOKEN = "ops-token"
SUPPORT_TOKEN = "support-token"
STRANGER_TOKEN = "stranger-token"
STRANGER = OPERATOR  # a principal with no driver profile of their own


def build_client(**settings_kwargs) -> tuple[TestClient, InMemoryWorkforceUnitOfWork]:
    uow = InMemoryWorkforceUnitOfWork()
    authorizer = FakeWorkforceAuthorizer(
        token_actors={
            APPLICANT_TOKEN: WorkforceActor(
                principal_id=APPLICANT,
                roles=frozenset({WorkforceRole.PICKUP_DRIVER}),
            ),
            OPS_TOKEN: WorkforceActor(
                principal_id=OPERATOR, roles=frozenset({WorkforceRole.OPERATIONS})
            ),
            SUPPORT_TOKEN: WorkforceActor(
                principal_id=SUPPORT, roles=frozenset({WorkforceRole.SUPPORT})
            ),
            STRANGER_TOKEN: WorkforceActor(
                principal_id=STRANGER,
                roles=frozenset({WorkforceRole.DRIVER_APPLICANT}),
            ),
        }
    )
    app = create_app(
        WorkforceSettings(environment=RuntimeEnvironment.TEST, **settings_kwargs),
        unit_of_work=uow,
        authorizer=authorizer,
    )
    return TestClient(app), uow


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def a_shift(weekday: int = 1) -> dict:
    return {
        "weekday": weekday,
        "slot": "MORNING",
        "starts_at": "09:00:00",
        "ends_at": "13:00:00",
    }


def an_application(client: TestClient, token: str = APPLICANT_TOKEN) -> dict:
    response = client.post(
        "/workforce/applications",
        json={
            "full_name": "Hussein Jabbar",
            "vehicle": {"kind": "MOTORCYCLE", "plate_number": "BG 12345"},
            "declared_shifts": [a_shift(day) for day in range(1, 8)],
            "terms_version": "driver-1.0",
            "documents_received": list(REQUIRED_OFFICE_DOCUMENTS),
        },
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def a_verified_driver(client: TestClient) -> dict:
    application = an_application(client)
    response = client.post(
        f"/workforce/applications/{application['application_id']}/verify",
        json={
            "office": "Karbala office",
            "documents_verified": list(REQUIRED_OFFICE_DOCUMENTS),
        },
        headers=auth(OPS_TOKEN),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ auth


def test_health_needs_no_token() -> None:
    client, _ = build_client()
    assert client.get("/health").status_code == 200


def test_a_missing_token_is_401() -> None:
    client, _ = build_client()
    assert client.get("/workforce/me/driver").status_code == 401


def test_the_default_composition_denies_everything() -> None:
    app = create_app(
        WorkforceSettings(environment=RuntimeEnvironment.TEST),
        unit_of_work=InMemoryWorkforceUnitOfWork(),
    )
    assert (
        TestClient(app).get("/workforce/me/driver", headers=auth(OPS_TOKEN)).status_code
        == 401
    )


def test_production_refuses_to_start_without_identity() -> None:
    with pytest.raises(ProductionStartupBlockedError) as caught:
        create_app(
            WorkforceSettings(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url="postgresql+psycopg://x/y",
            )
        )
    assert "WORKFORCE_IDENTITY_BASE_URL" in str(caught.value)


# ------------------------------------------------------------------ SEC-03


def test_an_application_starts_awaiting_the_office_visit() -> None:
    client, _ = build_client()

    application = an_application(client)

    assert application["status"] == "AWAITING_OFFICE_VERIFICATION"
    assert application["awaits_office_visit"] is True


def test_submitting_alone_produces_no_driver() -> None:
    client, _ = build_client()
    an_application(client)

    assert client.get("/workforce/me/driver", headers=auth(APPLICANT_TOKEN)).status_code == 404


def test_an_unverified_applicant_is_not_assignable() -> None:
    client, _ = build_client()
    an_application(client)

    body = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility", headers=auth(OPS_TOKEN)
    ).json()

    assert body["eligible"] is False
    assert body["reasons"] == ["NO_DRIVER_PROFILE"]


def test_only_operations_may_record_the_office_verification() -> None:
    """The gate is worthless if anyone can pass themselves through it."""
    client, _ = build_client()
    application = an_application(client)

    response = client.post(
        f"/workforce/applications/{application['application_id']}/verify",
        json={
            "office": "My kitchen",
            "documents_verified": list(REQUIRED_OFFICE_DOCUMENTS),
        },
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 403


def test_an_incomplete_checklist_is_refused_over_http() -> None:
    client, _ = build_client()
    application = an_application(client)

    response = client.post(
        f"/workforce/applications/{application['application_id']}/verify",
        json={"office": "Karbala office", "documents_verified": ["ID_CARD"]},
        headers=auth(OPS_TOKEN),
    )

    assert response.status_code == 422
    assert set(response.json()["detail"]["missing"]) == {
        "DRIVING_LICENCE",
        "VEHICLE_REGISTRATION",
        "INSURANCE_CERTIFICATE",
    }


def test_the_office_visit_creates_the_driver() -> None:
    client, _ = build_client()

    driver = a_verified_driver(client)

    assert driver["status"] == "ACTIVE"
    assert driver["principal_id"] == str(APPLICANT)


def test_another_applicants_case_answers_404_not_403() -> None:
    client, _ = build_client()
    application = an_application(client)

    response = client.get(
        f"/workforce/applications/{application['application_id']}",
        headers=auth(STRANGER_TOKEN),
    )

    assert response.status_code == 404


def test_operations_may_read_any_application() -> None:
    client, _ = build_client()
    application = an_application(client)

    response = client.get(
        f"/workforce/applications/{application['application_id']}", headers=auth(OPS_TOKEN)
    )

    assert response.status_code == 200


def test_an_application_needs_at_least_one_shift() -> None:
    client, _ = build_client()

    response = client.post(
        "/workforce/applications",
        json={
            "full_name": "Hussein Jabbar",
            "vehicle": {"kind": "MOTORCYCLE", "plate_number": "BG 12345"},
            "declared_shifts": [],
            "terms_version": "driver-1.0",
        },
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 422


def test_an_unusable_plate_number_is_refused() -> None:
    client, _ = build_client()

    response = client.post(
        "/workforce/applications",
        json={
            "full_name": "Hussein Jabbar",
            "vehicle": {"kind": "MOTORCYCLE", "plate_number": "!!!"},
            "declared_shifts": [a_shift()],
            "terms_version": "driver-1.0",
        },
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ DRV-A01/A04


def test_a_driver_reads_and_changes_only_their_own_pattern() -> None:
    """There is no route that names another driver's pattern at all."""
    client, _ = build_client()
    a_verified_driver(client)

    response = client.put(
        "/workforce/me/shift-pattern",
        json={"windows": [a_shift(2)]},
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert not [
        path for path in paths if "shift-pattern" in path and "{driver_id}" in path
    ]


def test_a_pattern_change_takes_effect_tomorrow_over_http() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    body = client.put(
        "/workforce/me/shift-pattern",
        json={"windows": [a_shift(2)]},
        headers=auth(APPLICANT_TOKEN),
    ).json()

    tomorrow = datetime.now(tz=UTC).date() + timedelta(days=1)
    assert date.fromisoformat(body["effective_from"]) == tomorrow


def test_starting_a_shift_records_attendance() -> None:
    """The fixture declares all seven days, so this does not depend on the run day."""
    client, _ = build_client()
    a_verified_driver(client)

    response = client.post("/workforce/me/shift/start", headers=auth(APPLICANT_TOKEN))

    assert response.status_code == 200
    body = response.json()
    assert body["attendance"]["status"] == "STARTED"
    assert body["attendance"]["scheduled_start"] == "09:00:00"


def test_a_shift_cannot_be_started_twice_over_http() -> None:
    client, _ = build_client()
    a_verified_driver(client)
    client.post("/workforce/me/shift/start", headers=auth(APPLICANT_TOKEN))

    response = client.post("/workforce/me/shift/start", headers=auth(APPLICANT_TOKEN))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "shift_already_started"


def test_a_driver_with_no_shift_that_day_is_told_so() -> None:
    """A pattern covering only Monday leaves every other day unscheduled."""
    client, _ = build_client()
    application = an_application(client, token=STRANGER_TOKEN)
    client.post(
        f"/workforce/applications/{application['application_id']}/verify",
        json={
            "office": "Karbala office",
            "documents_verified": list(REQUIRED_OFFICE_DOCUMENTS),
        },
        headers=auth(OPS_TOKEN),
    )

    body = client.get(
        f"/workforce/principals/{STRANGER}/eligibility?day=2026-09-14",
        headers=auth(OPS_TOKEN),
    ).json()

    assert "SHIFT_NOT_STARTED" in body["reasons"]


def test_a_driver_without_a_profile_cannot_start_a_shift() -> None:
    client, _ = build_client()

    response = client.post("/workforce/me/shift/start", headers=auth(APPLICANT_TOKEN))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "driver_not_found"


# ------------------------------------------------------------------ OPS-09


def test_a_driver_cannot_clear_their_own_lateness_block() -> None:
    """The rule holds in the domain too, so deleting the route check is not enough."""
    client, _ = build_client()
    a_verified_driver(client)

    response = client.post(
        f"/workforce/blocks/{uuid4()}/clear",
        json={"note": "it was fine really"},
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "only_support_may_clear_a_block"


def test_a_driver_cannot_see_the_support_block_queue() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    denied = client.get("/workforce/blocks", headers=auth(APPLICANT_TOKEN))
    allowed = client.get("/workforce/blocks", headers=auth(SUPPORT_TOKEN))

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_a_driver_cannot_approve_their_own_leave() -> None:
    client, _ = build_client()
    a_verified_driver(client)
    leave = client.post(
        "/workforce/me/leave",
        json={
            "reason": "SICK",
            "kind": "FULL_DAY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    ).json()

    response = client.post(
        f"/workforce/leave/{leave['leave_id']}/decision",
        json={"approve": True},
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "only_support_may_decide_leave"


def test_support_approves_leave() -> None:
    client, _ = build_client()
    a_verified_driver(client)
    leave = client.post(
        "/workforce/me/leave",
        json={
            "reason": "SICK",
            "kind": "FULL_DAY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    ).json()

    response = client.post(
        f"/workforce/leave/{leave['leave_id']}/decision",
        json={"approve": True},
        headers=auth(SUPPORT_TOKEN),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"


def test_a_decline_without_a_reason_is_refused() -> None:
    client, _ = build_client()
    a_verified_driver(client)
    leave = client.post(
        "/workforce/me/leave",
        json={
            "reason": "PERSONAL",
            "kind": "FULL_DAY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    ).json()

    response = client.post(
        f"/workforce/leave/{leave['leave_id']}/decision",
        json={"approve": False},
        headers=auth(SUPPORT_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "leave_decision_note_required"


def test_a_driver_cannot_see_the_pending_leave_queue() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    denied = client.get("/workforce/leave/pending", headers=auth(APPLICANT_TOKEN))
    allowed = client.get("/workforce/leave/pending", headers=auth(SUPPORT_TOKEN))

    assert denied.status_code == 403
    assert allowed.status_code == 200


# ------------------------------------------------------------------ DRV-A03


def test_hourly_leave_must_state_its_hours_over_http() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    response = client.post(
        "/workforce/me/leave",
        json={
            "reason": "PERSONAL",
            "kind": "HOURLY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_leave_window"


def test_an_invented_leave_reason_is_rejected_by_the_schema() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    response = client.post(
        "/workforce/me/leave",
        json={
            "reason": "HOLIDAY",
            "kind": "FULL_DAY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    )

    assert response.status_code == 422


def test_a_driver_sees_only_their_own_leave() -> None:
    client, _ = build_client()
    a_verified_driver(client)
    client.post(
        "/workforce/me/leave",
        json={
            "reason": "SICK",
            "kind": "FULL_DAY",
            "start_date": "2026-09-14",
            "end_date": "2026-09-14",
        },
        headers=auth(APPLICANT_TOKEN),
    )

    mine = client.get("/workforce/me/leave", headers=auth(APPLICANT_TOKEN)).json()

    assert len(mine) == 1
    assert mine[0]["reference"].startswith("LV-")


# ------------------------------------------------------------------ eligibility


def test_a_principal_may_read_their_own_eligibility() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    response = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility", headers=auth(APPLICANT_TOKEN)
    )

    assert response.status_code == 200
    assert response.json()["driver_id"] is not None


def test_reading_another_principals_eligibility_needs_support_or_operations() -> None:
    """This is the route Pickup and Delivery call with a service credential."""
    client, _ = build_client()
    a_verified_driver(client)

    denied = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility", headers=auth(STRANGER_TOKEN)
    )
    allowed = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility", headers=auth(OPS_TOKEN)
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_eligibility_reports_its_reasons_not_just_a_verdict() -> None:
    client, _ = build_client()
    a_verified_driver(client)

    body = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility?day=2026-09-19",
        headers=auth(OPS_TOKEN),
    ).json()

    assert body["eligible"] is False
    assert "SHIFT_NOT_STARTED" in body["reasons"]


def test_a_day_the_driver_does_not_work_is_named_as_such() -> None:
    """A Monday-only pattern leaves Saturday 2026-09-19 unscheduled."""
    client, _ = build_client()
    a_verified_driver(client)
    client.put(
        "/workforce/me/shift-pattern",
        json={"windows": [a_shift(1)]},
        headers=auth(APPLICANT_TOKEN),
    )

    body = client.get(
        f"/workforce/principals/{APPLICANT}/eligibility?day=2026-09-19",
        headers=auth(OPS_TOKEN),
    ).json()

    assert "NO_SHIFT_TODAY" in body["reasons"]


def test_an_unknown_principal_is_simply_not_a_driver() -> None:
    client, _ = build_client()

    body = client.get(
        f"/workforce/principals/{uuid4()}/eligibility", headers=auth(OPS_TOKEN)
    ).json()

    assert body["driver_id"] is None
    assert body["reasons"] == ["NO_DRIVER_PROFILE"]
